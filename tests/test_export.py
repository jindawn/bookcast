"""Offline M4B container, chapter, cache, and CLI regression tests."""

import json
import shutil
import struct
import subprocess
import zlib
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from bookcast.cli import app
from bookcast.errors import BookCastError
from bookcast.export import export_m4b, read_valid_export
from bookcast.pipeline import Pipeline
from bookcast.providers import MockLLMProvider, MockTTSProvider
from bookcast.storage import sha256_file, write_json


pytestmark = pytest.mark.skipif(not shutil.which('ffmpeg') or not shutil.which('ffprobe'), reason='FFmpeg required')


@pytest.fixture(scope='module')
def source_job(tmp_path_factory):
    root = tmp_path_factory.mktemp('m4b-fixture')
    source = root / '多章 样本.txt'
    source.write_text('Chapter 1\nClear first idea.\nChapter 2\nA different second idea.\nChapter 3\nA third idea.', encoding='utf-8')
    return Pipeline(MockLLMProvider(), MockTTSProvider(), root / 'out').generate(source, mode='two_host', minutes=2)


@pytest.fixture
def job(source_job, tmp_path):
    target = tmp_path / '中文 空格' / 'job'
    shutil.copytree(source_job, target)
    return target


def probe(path):
    result = subprocess.run(['ffprobe', '-v', 'error', '-show_format', '-show_streams',
                             '-show_chapters', '-of', 'json', str(path)],
                            capture_output=True, text=True, check=True)
    return json.loads(result.stdout)


def image(path):
    def chunk(tag, data):
        return struct.pack('!I', len(data)) + tag + data + struct.pack('!I', zlib.crc32(tag + data))
    header = b'\x89PNG\r\n\x1a\n'
    ihdr = struct.pack('!IIBBBBB', 2, 2, 8, 2, 0, 0, 0)
    row = b'\x00' + b'\xff\x00\x00' * 2
    path.write_bytes(header + chunk(b'IHDR', ihdr) + chunk(b'IDAT', zlib.compress(row * 2)) + chunk(b'IEND', b''))


def test_multiple_chapters_unicode_metadata_and_no_provider_calls(job):
    plan = json.loads((job / 'plans/episode.json').read_text())
    metadata = json.loads((job / 'metadata.json').read_text())
    metadata.update(title='测试：协作与分工', authors=['张三'], language='zh')
    write_json(job / 'metadata.json', metadata)
    with patch.object(MockLLMProvider, 'generate_structured', side_effect=AssertionError('LLM called')), \
         patch.object(MockTTSProvider, 'synthesize', side_effect=AssertionError('TTS called')):
        record, reused = export_m4b(job)
    assert not reused and len(record.chapters) == len(plan['segments']) > 1
    assert all(c.kind == 'podcast_segment' for c in record.chapters)
    assert record.chapters[0].start_ms == 0
    assert [c.title for c in record.chapters] == [s['title'] for s in plan['segments']]
    info = probe(job / 'podcast.m4b')
    assert 'mp4' in info['format']['format_name']
    assert info['format']['tags']['major_brand'] == 'M4B '
    assert any(s['codec_name'] == 'aac' and s['codec_type'] == 'audio' and s.get('tags', {}).get('language') == 'zho'
               for s in info['streams'])
    assert len(info['chapters']) == len(record.chapters)
    assert [c['tags']['title'] for c in info['chapters']] == [s['title'] for s in plan['segments']]
    assert info['format']['tags']['artist'] == '张三'
    assert info['format']['tags']['title'] == '测试：协作与分工'
    assert record.chapters[-1].end_ms == round(float(info['format']['duration']) * 1000)
    assert (job / 'podcast.mp3').is_file()


def test_single_chapter_and_old_job_without_plan(job):
    plan = json.loads((job / 'plans/episode.json').read_text())
    plan['segments'] = plan['segments'][:1]
    write_json(job / 'plans/episode.json', plan)
    # Use the first segment as a one-chapter historical job; replace the MP3
    # with the corresponding real WAV and record its new completed merge hash.
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-i', str(job / 'audio/0001.wav'),
                    '-c:a', 'libmp3lame', str(job / 'podcast.mp3')], check=True)
    manifest = json.loads((job / 'manifest.json').read_text())
    manifest['steps']['merge']['artifacts']['podcast.mp3'] = sha256_file(job / 'podcast.mp3')
    write_json(job / 'manifest.json', manifest)
    record, _ = export_m4b(job)
    assert len(record.chapters) == 1 and len(probe(job / 'podcast.m4b')['chapters']) == 1
    assert record.chapters[0].kind == 'podcast_segment'
    # Older manifests have no episode plan; source chapter titles are used.
    (job / 'plans/episode.json').unlink()
    metadata = json.loads((job / 'metadata.json').read_text())
    metadata['chapter_ids'] = ['0001']
    write_json(job / 'metadata.json', metadata)
    old, reused = export_m4b(job)
    assert not reused and old.chapters[0].kind == 'source_chapter'
    assert old.chapters[0].title == json.loads((job / 'chapters/0001.json').read_text())['title']


def test_cover_optional_and_user_fixture(job, tmp_path):
    without, _ = export_m4b(job)
    assert without.cover_sha256 is None
    assert not any(s.get('disposition', {}).get('attached_pic') for s in probe(job / 'podcast.m4b')['streams'])
    cover = tmp_path / '封面.png'
    image(cover)
    with_cover, reused = export_m4b(job, cover)
    assert not reused and with_cover.cover_sha256 == sha256_file(cover)
    assert any(s.get('disposition', {}).get('attached_pic') for s in probe(job / 'podcast.m4b')['streams'])
    bad = tmp_path / 'bad.png'
    bad.write_bytes(b'not a picture')
    with pytest.raises(BookCastError):
        export_m4b(job, bad)


def test_source_attribution_excludes_private_urls_and_paths(job):
    metadata = json.loads((job / 'metadata.json').read_text())
    metadata['acquisition'] = {'source': {'provider': 'gutenberg',
                                          'url': 'https://example.invalid/private?token=do-not-copy',
                                          'local_path': '/private/book.txt'}}
    write_json(job / 'metadata.json', metadata)
    record, _ = export_m4b(job)
    assert record.source_attribution == 'Project Gutenberg 公开资源'
    for path in (job / 'exports/m4b.json', job / 'exports/chapters.ffmeta'):
        data = path.read_text(encoding='utf-8')
        assert 'do-not-copy' not in data and '/private/book.txt' not in data


def test_two_letter_language_is_written_as_mp4_audio_language(job):
    metadata = json.loads((job / 'metadata.json').read_text())
    metadata['language'] = 'fr-FR'
    write_json(job / 'metadata.json', metadata)
    record, _ = export_m4b(job)
    assert record.language == 'fr-FR'
    assert next(s for s in probe(job / 'podcast.m4b')['streams'] if s['codec_type'] == 'audio')['tags']['language'] == 'fra'


def test_idempotent_export_and_cli(job):
    runner = CliRunner()
    first = runner.invoke(app, ['export', str(job), '--format', 'm4b'])
    assert first.exit_code == 0, first.output
    before = (job / 'podcast.m4b').stat().st_mtime_ns
    second = runner.invoke(app, ['export', str(job), '--format', 'm4b'])
    assert second.exit_code == 0 and '复用' in second.output
    assert (job / 'podcast.m4b').stat().st_mtime_ns == before
    assert runner.invoke(app, ['export', str(job), '--format', 'wav']).exit_code == 1
    assert read_valid_export(job) is not None


def test_audio_change_invalidates_cache(job):
    export_m4b(job)
    original = sha256_file(job / 'podcast.m4b')
    changed = job / 'changed.mp3'
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-i', str(job / 'podcast.mp3'),
                    '-af', 'volume=0.7', str(changed)], check=True)
    changed.replace(job / 'podcast.mp3')
    assert read_valid_export(job) is None
    manifest = json.loads((job / 'manifest.json').read_text())
    manifest['steps']['merge']['artifacts']['podcast.mp3'] = sha256_file(job / 'podcast.mp3')
    write_json(job / 'manifest.json', manifest)
    newer, reused = export_m4b(job)
    assert not reused and sha256_file(job / 'podcast.m4b') != original
    assert newer.output_sha256 == sha256_file(job / 'podcast.m4b')


def test_corrupted_wav_and_mp3_rejected(job):
    wav = job / 'audio/0001.wav'
    wav.write_bytes(b'broken')
    with pytest.raises(BookCastError):
        export_m4b(job)
    job.joinpath('podcast.mp3').write_bytes(b'broken')
    with pytest.raises(BookCastError):
        export_m4b(job)

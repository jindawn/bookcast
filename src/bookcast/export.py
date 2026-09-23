"""Independent, resumable media export from completed Core audio artifacts."""

import json
import shutil
import subprocess
from pathlib import Path
from typing import Literal

from pydantic import Field

from .audio import validate_wav, wav_seconds
from .content_models import EpisodePlan
from .errors import BookCastError
from .models import BookMetadata, Chapter, Model, utc_now
from .pipeline import artifacts_valid, load_manifest
from .storage import artifact_path, atomic_target, fingerprint, job_lock, sha256_file, write_json


class ExportChapter(Model):
    kind: Literal['podcast_segment', 'source_chapter']
    title: str
    source_chapter_ids: list[str]
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)


class M4BExport(Model):
    schema_version: Literal[1] = 1
    format: Literal['m4b'] = 'm4b'
    title: str
    author: str
    language: str
    mode: str
    duration_seconds: float = Field(gt=0)
    source_attribution: str
    chapters: list[ExportChapter] = Field(min_length=1)
    cover_sha256: str | None = None
    input_hash: str
    output_sha256: str
    created_at: str


def _probe(path: Path) -> dict:
    executable = shutil.which('ffprobe')
    if not executable:
        raise BookCastError('找不到 ffprobe；请安装 FFmpeg。')
    result = subprocess.run([executable, '-v', 'error', '-show_format', '-show_streams',
                             '-show_chapters', '-of', 'json', str(path)],
                            capture_output=True, text=True, timeout=60)
    if result.returncode:
        raise BookCastError('音频或封面无法由 ffprobe 读取。')
    try:
        return json.loads(result.stdout)
    except ValueError as exc:
        raise BookCastError('ffprobe 返回了无效结果。') from exc


def _audio_duration(path: Path, codec: str) -> float:
    probe = _probe(path)
    if not any(s.get('codec_type') == 'audio' and s.get('codec_name') == codec
               for s in probe.get('streams', [])):
        raise BookCastError(f'音频不是有效的 {codec}。')
    try:
        duration = float(probe['format']['duration'])
    except (KeyError, TypeError, ValueError) as exc:
        raise BookCastError('音频缺少有效时长。') from exc
    if not 0 < duration < 86400 * 30:
        raise BookCastError('音频时长无效。')
    return duration


def _cover_hash(cover: Path | None) -> str | None:
    if cover is None:
        return None
    if cover.is_symlink() or not cover.is_file() or cover.stat().st_size > 10 * 1024 * 1024:
        raise BookCastError('封面必须是用户提供的 10 MB 以内本地图片，且不能是符号链接。')
    with cover.open('rb') as stream:
        header = stream.read(8)
    if not (header.startswith(b'\xff\xd8\xff') or header == b'\x89PNG\r\n\x1a\n'):
        raise BookCastError('封面仅支持有效 JPEG 或 PNG。')
    if not any(s.get('codec_type') == 'video' and s.get('codec_name') in {'png', 'mjpeg'}
               for s in _probe(cover).get('streams', [])):
        raise BookCastError('封面图片无法解码。')
    return sha256_file(cover)


def _chapter_sources(root: Path, metadata: BookMetadata) -> tuple[str, list[tuple[str, str, list[str], Path]]]:
    plan_path = artifact_path(root, 'plans/episode.json')
    if plan_path.is_file():
        plan = EpisodePlan.model_validate_json(plan_path.read_text(encoding='utf-8'))
        return plan.mode, [(s.id, s.title, s.chapter_ids, artifact_path(root, f'audio/{s.id}.wav'))
                           for s in plan.segments]
    # Phase 1-3 jobs have one WAV per source chapter and no episode plan.
    result = []
    for cid in metadata.chapter_ids:
        chapter = Chapter.model_validate_json(artifact_path(root, f'chapters/{cid}.json').read_text(encoding='utf-8'))
        result.append((cid, chapter.title, [cid], artifact_path(root, f'audio/{cid}.wav')))
    return 'legacy', result


def _source_attribution(metadata: BookMetadata) -> str:
    source = metadata.acquisition or {}
    # Deliberately do not store URLs, source_name, provider settings, or absolute paths.
    offer = source.get('source') if isinstance(source.get('source'), dict) else source
    provider = str(offer.get('provider', '')).lower()
    return 'Project Gutenberg 公开资源' if provider == 'gutenberg' else '用户提供的文件或链接'


def _ffmeta_escape(value: str) -> str:
    return value.replace('\\', '\\\\').replace('=', '\\=').replace(';', '\\;').replace('#', '\\#').replace('\n', ' ')


def _mp4_language(value: str) -> str:
    """MP4 language atoms use ISO 639-2/3, not BCP 47 or two-letter codes."""
    code = value.lower().replace('_', '-').split('-', 1)[0]
    common = {'zh': 'zho', 'en': 'eng', 'fr': 'fra', 'de': 'deu', 'es': 'spa',
              'ja': 'jpn', 'pt': 'por', 'ru': 'rus', 'it': 'ita', 'ko': 'kor'}
    return common.get(code, code if len(code) == 3 and code.isascii() and code.isalpha() else 'und')


def read_valid_export(root: Path) -> M4BExport | None:
    """Read-only visibility gate for Web; stale or changed audio is never served."""
    try:
        sidecar = artifact_path(root, 'exports/m4b.json')
        audio = artifact_path(root, 'podcast.mp3')
        output = artifact_path(root, 'podcast.m4b')
        if not (sidecar.is_file() and audio.is_file() and output.is_file()):
            return None
        record = M4BExport.model_validate_json(sidecar.read_text(encoding='utf-8'))
        if sha256_file(output) != record.output_sha256:
            return None
        inputs = json.loads(artifact_path(root, 'exports/m4b-inputs.json').read_text(encoding='utf-8'))
        if fingerprint(inputs) != record.input_hash or sha256_file(audio) != inputs['mp3_sha256']:
            return None
        metadata_path = artifact_path(root, 'metadata.json')
        if sha256_file(metadata_path) != inputs['metadata_sha256']:
            return None
        plan_path = artifact_path(root, 'plans/episode.json')
        if (sha256_file(plan_path) if plan_path.is_file() else None) != inputs['plan_sha256']:
            return None
        metadata = BookMetadata.model_validate_json(metadata_path.read_text(encoding='utf-8'))
        _, parts = _chapter_sources(root, metadata)
        if [sha256_file(part[3]) for part in parts] != inputs['wav_sha256']:
            return None
        return record
    except (OSError, ValueError, KeyError, BookCastError):
        return None


def export_m4b(root: Path, cover: Path | None = None) -> tuple[M4BExport, bool]:
    """Export completed audio only; return (record, reused). Never calls AI/TTS."""
    root = root.resolve()
    with job_lock(root):
        manifest = load_manifest(artifact_path(root, 'manifest.json'))
        if manifest.status != 'completed' or 'merge' not in manifest.steps or not artifacts_valid(root, manifest.steps['merge']):
            raise BookCastError('只有完整、有效的已完成音频任务可以导出 M4B。')
        mp3 = artifact_path(root, 'podcast.mp3')
        duration = _audio_duration(mp3, 'mp3')
        metadata_path = artifact_path(root, 'metadata.json')
        metadata = BookMetadata.model_validate_json(metadata_path.read_text(encoding='utf-8'))
        mode, parts = _chapter_sources(root, metadata)
        if not parts:
            raise BookCastError('任务没有可导出的音频章节。')
        measured = []
        for _, _, _, wav in parts:
            try:
                validate_wav(wav)
                measured.append(wav_seconds(wav))
            except (OSError, ValueError) as exc:
                raise BookCastError('章节 WAV 损坏，不能构造可信的章节时间。') from exc
        total = sum(measured)
        if abs(total - duration) > max(2.0, total * 0.01):
            raise BookCastError('章节 WAV 与最终 MP3 时长不一致；请先恢复任务。')
        cover_digest = _cover_hash(cover)
        inputs = {'contract': 'm4b-aac-chapters-v3', 'mp3_sha256': sha256_file(mp3),
                  'metadata_sha256': sha256_file(metadata_path),
                  'plan_sha256': sha256_file(artifact_path(root, 'plans/episode.json'))
                  if artifact_path(root, 'plans/episode.json').is_file() else None,
                  'wav_sha256': [sha256_file(part[3]) for part in parts], 'cover_sha256': cover_digest}
        input_hash = fingerprint(inputs)
        current = read_valid_export(root)
        if current and current.input_hash == input_hash:
            return current, True

        # Boundaries come from measured WAV frames, scaled by the final MP3's
        # actual probed duration to account for encoder padding, never budget estimates.
        end_ms = round(duration * 1000)
        cumulative = 0.0
        chapters = []
        for index, ((_, title, source_ids, _), seconds) in enumerate(zip(parts, measured, strict=True)):
            start = chapters[-1].end_ms if chapters else 0
            cumulative += seconds
            end = end_ms if index == len(parts) - 1 else round(end_ms * cumulative / total)
            if end <= start:
                raise BookCastError('音频章节过短，无法生成毫秒级章节标记。')
            chapters.append(ExportChapter(kind='podcast_segment' if mode != 'legacy' else 'source_chapter',
                                          title=title, source_chapter_ids=source_ids,
                                          start_ms=start, end_ms=end))
        author = '、'.join(metadata.authors) if metadata.authors else '未知作者'
        language = metadata.language or 'und'
        mp4_language = _mp4_language(language)
        attribution = _source_attribution(metadata)
        ffmeta = [';FFMETADATA1', f'title={_ffmeta_escape(metadata.title)}',
                  f'artist={_ffmeta_escape(author)}', f'album={_ffmeta_escape(metadata.title)}',
                  f'language={_ffmeta_escape(language)}', f'comment={_ffmeta_escape(attribution)}']
        for chapter in chapters:
            ffmeta.extend(['[CHAPTER]', 'TIMEBASE=1/1000', f'START={chapter.start_ms}',
                           f'END={chapter.end_ms}', f'title={_ffmeta_escape(chapter.title)}'])
        meta_path = artifact_path(root, 'exports/chapters.ffmeta')
        write_path = artifact_path(root, 'podcast.m4b')
        executable = shutil.which('ffmpeg')
        if not executable:
            raise BookCastError('找不到 FFmpeg；请安装后重试导出。')
        with atomic_target(meta_path) as temporary:
            temporary.write_text('\n'.join(ffmeta) + '\n', encoding='utf-8')
        with atomic_target(write_path) as temporary:
            command = [executable, '-hide_banner', '-loglevel', 'error', '-xerror', '-nostdin', '-y',
                       '-i', str(mp3), '-f', 'ffmetadata', '-i', str(meta_path)]
            if cover:
                command += ['-i', str(cover)]
            command += ['-map', '0:a:0', '-map_metadata', '1', '-map_chapters', '1',
                        '-metadata:s:a:0', f'language={mp4_language}', '-c:a', 'aac', '-b:a', '96k']
            if cover:
                command += ['-map', '2:v:0', '-c:v', 'copy', '-disposition:v:0', 'attached_pic']
            command += ['-brand', 'M4B ', '-f', 'ipod', str(temporary)]
            result = subprocess.run(command, capture_output=True, timeout=1800)
            if result.returncode:
                raise BookCastError('FFmpeg M4B 编码失败；请检查源音频及封面。')
            final_probe = _probe(temporary)
            if not any(s.get('codec_name') == 'aac' for s in final_probe.get('streams', [])) or len(final_probe.get('chapters', [])) != len(chapters):
                raise BookCastError('M4B 编码结果缺少 AAC 音频或章节。')
            final_duration = float(final_probe['format']['duration'])
            if abs(final_duration - duration) > 1.0:
                raise BookCastError('M4B 与源 MP3 时长不一致。')
        record = M4BExport(title=metadata.title, author=author, language=language, mode=mode,
                           duration_seconds=final_duration, source_attribution=attribution,
                           chapters=chapters, cover_sha256=cover_digest, input_hash=input_hash,
                           output_sha256=sha256_file(write_path), created_at=utc_now())
        write_json(artifact_path(root, 'exports/m4b-inputs.json'), inputs)
        write_json(artifact_path(root, 'exports/m4b.json'), record.model_dump(mode='json'))
        return record, False

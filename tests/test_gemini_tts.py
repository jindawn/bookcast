"""Offline public-wire fixtures and real Core checkpoint/failure tests."""
import base64
import io
import json
from pathlib import Path
import struct
import subprocess
import sys
import time
from types import SimpleNamespace
from urllib.error import HTTPError, URLError
import wave

import pytest
from pydantic import ValidationError

from bookcast.adapters.gemini import GeminiTTSProvider, classify_http, decode_audio, AudioPayload, NoRedirect
from bookcast.audio import validate_wav
from bookcast.errors import BookCastError
from bookcast.models import PodcastScript, DialogueTurn
from bookcast.pipeline import Pipeline, load_manifest
from bookcast.provider_api import (ErrorKind, ProviderError, ProviderCapabilities, SpeechSegment,
                                  SpeechTurn, SegmentSpeechInfo)
from bookcast.provider_chain import ProviderChain
from bookcast.provider_config import ProviderSpec, CloudTTSConfig
from bookcast.providers import MockLLMProvider, MockTTSProvider
from bookcast.speech_segments import speech_segments
from bookcast.storage import sha256_file

DEMO = Path(__file__).resolve().parents[1] / 'examples/content-demo.txt'


def spec(**values):
    return ProviderSpec(name='gemini', kind='tts', type='gemini-tts', model='gemini-3.1-flash-tts-preview',
                        api_key_env='GEMINI_API_KEY', cloud_tts=CloudTTSConfig(send_text_to_cloud=True, **values))


def segment():
    return SpeechSegment(turns=[SpeechTurn(speaker='主持人', text='分工有收益。'),
                                SpeechTurn(speaker='嘉宾', text='那沟通成本呢？')])


def wav_bytes():
    output = io.BytesIO()
    with wave.open(output, 'wb') as wav:
        wav.setparams((1, 2, 24000, 0, 'NONE', 'not compressed'))
        wav.writeframes(struct.pack('<hh', 1000, -1000) * 2400)
    return output.getvalue()


def response():
    return {'status': 'completed', 'model': 'gemini-3.1-flash-tts-preview',
            'usage_metadata': {'prompt_token_count': 12, 'candidates_token_count': 25},
            'steps': [{'type': 'model_output', 'content': [{
                'type': 'audio', 'mime_type': 'audio/wav',
                'data': base64.b64encode(wav_bytes()).decode()}]}]}


def test_official_request_audio_voices_usage_and_no_secret(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv('GEMINI_API_KEY', 'fake-secret-test-only')
    sent = []
    def opening(req, timeout):
        sent.append(req)
        return io.BytesIO(json.dumps(response()).encode())
    monkeypatch.setattr('bookcast.adapters.gemini.request.build_opener', lambda *a: SimpleNamespace(open=opening))
    provider = GeminiTTSProvider(spec(style_instruction='自然中文问答'))
    target = tmp_path/'audio.wav'
    info = provider.synthesize_segment(segment(), target)
    validate_wav(target)
    with wave.open(str(target)) as wav:
        assert wav.readframes(2) == struct.pack('<hh', 1000, -1000)
    assert info.voices == {'主持人': 'Kore', '嘉宾': 'Puck'}
    req = sent[0]; payload = json.loads(req.data)
    assert req.full_url == 'https://generativelanguage.googleapis.com/v1beta/interactions'
    assert req.headers['X-goog-api-key'] == 'fake-secret-test-only'
    assert 'fake-secret' not in str(payload) + provider.cache_key + str(info) + capsys.readouterr().err
    configs = payload['generation_config']['speech_config']['speakers']
    assert [c['speaker'] for c in configs] == ['Host', 'Guest']
    assert [c['voice'] for c in configs] == ['Kore', 'Puck']
    assert [p['text'] for p in payload['input'][0]['content']] == ['分工有收益。', '那沟通成本呢？']
    assert provider.last_usage.input_tokens == 12 and provider.last_usage.reasoning_tokens is None
    provider.synthesize_segment(SpeechSegment(turns=[segment().turns[0]]), target)
    assert json.loads(sent[1].data)['generation_config']['speech_config']['speakers'] == configs


@pytest.mark.parametrize('status,body,kind', [
    (400, {'error': {'details': [{'reason': 'API_KEY_INVALID'}]}}, ErrorKind.AUTH),
    (401, {}, ErrorKind.AUTH), (403, {}, ErrorKind.PERMISSION), (402, {}, ErrorKind.QUOTA),
    (429, {'error': {'details': [{'violations': [{'quotaId': 'RequestsPerDayPerProject'}]}]}}, ErrorKind.QUOTA),
    (429, {'error': {'details': [{'violations': [{'quotaValue': '0'}]}]}}, ErrorKind.QUOTA),
    (429, {'error': {'details': [{'violations': [{'quotaId': 'RequestsPerMinutePerProject'}]}]}}, ErrorKind.RATE_LIMIT),
    (429, {}, ErrorKind.RATE_LIMIT), (408, {}, ErrorKind.TIMEOUT), (504, {}, ErrorKind.TIMEOUT),
    (500, {}, ErrorKind.UNAVAILABLE), (503, {}, ErrorKind.UNAVAILABLE),
    (400, {}, ErrorKind.SCHEMA), (404, {}, ErrorKind.SCHEMA),
    (400, {'error': {'details': [{'reason': 'BILLING_DISABLED'}]}}, ErrorKind.QUOTA),
    (400, {'error': ['not-object']}, ErrorKind.SCHEMA),
])
def test_safe_error_classification(status, body, kind):
    failure = classify_http(status, json.dumps(body).encode())
    assert failure.kind == kind
    assert failure.retryable == (kind in {ErrorKind.QUOTA, ErrorKind.RATE_LIMIT, ErrorKind.TIMEOUT, ErrorKind.UNAVAILABLE})


@pytest.mark.parametrize('mode', ['timeout', 'network', 'http', 'json', 'redirect'])
def test_request_failures_are_safe_with_existing_retry_policy(monkeypatch, mode):
    monkeypatch.setenv('GEMINI_API_KEY', 'SECRET')
    calls = []
    def opening(*args, **kwargs):
        calls.append(1)
        if mode == 'timeout': raise TimeoutError('SECRET')
        if mode == 'network': raise URLError('SECRET')
        if mode == 'http': raise HTTPError('https://example', 403, 'SECRET', {}, io.BytesIO(b'SECRET'))
        if mode == 'redirect': return NoRedirect().redirect_request(None, None, 302, '', {}, 'https://other')
        return io.BytesIO(b'SECRET not JSON')
    monkeypatch.setattr('bookcast.adapters.gemini.request.build_opener', lambda *a: SimpleNamespace(open=opening))
    with pytest.raises(ProviderError) as exc: GeminiTTSProvider(spec())._request({})
    assert 'SECRET' not in str(exc.value)
    assert len(calls) == (5 if mode in {'timeout', 'network'} else 1)


def test_missing_api_key_fails_before_transport(monkeypatch):
    monkeypatch.delenv('GEMINI_API_KEY', raising=False)
    with pytest.raises(ProviderError) as exc:
        GeminiTTSProvider(spec())._request({})
    assert exc.value.kind == ErrorKind.AUTH


@pytest.mark.parametrize('mode', ['missing_audio', 'failed', 'incomplete', 'cancelled'])
def test_interaction_without_completed_audio_is_permanent(tmp_path, mode):
    data = response()
    if mode == 'missing_audio':
        data['steps'][0]['content'] = [{'type': 'text', 'text': 'no audio'}]
    else:
        data['status'] = mode
        data['error'] = {'message': 'unavailable'}
    with pytest.raises(ProviderError) as exc:
        decode_audio(data, tmp_path/'invalid.wav')
    assert exc.value.kind in {ErrorKind.SCHEMA, ErrorKind.BUSINESS}
    assert not (tmp_path/'invalid.wav').exists()


def test_canonical_decoder_valid_wav(tmp_path):
    dest = tmp_path / "valid.wav"
    raw_wav = wav_bytes()
    resp = {
        'status': 'completed',
        'steps': [{
            'type': 'model_output',
            'content': [{
                'type': 'audio',
                'mime_type': 'audio/wav',
                'data': base64.b64encode(raw_wav).decode()
            }]
        }]
    }
    payload = decode_audio(resp, dest)
    assert isinstance(payload, AudioPayload)
    assert payload.audio_bytes == raw_wav
    assert payload.bytes == raw_wav
    assert payload.data == raw_wav
    assert payload.container == 'wav'
    assert payload.codec == 'pcm_s16le'
    assert payload.sample_rate == 24000
    assert payload.channels == 1
    assert dest.is_file()
    assert dest.read_bytes() == raw_wav

    # Without destination parameter
    in_mem_payload = decode_audio(resp)
    assert isinstance(in_mem_payload, AudioPayload)
    assert in_mem_payload.bytes == raw_wav


def test_canonical_decoder_invalid_riff(tmp_path):
    dest = tmp_path / "invalid_riff.wav"
    bad_data = b"NOT_A_RIFF_AUDIO_DATA_AT_ALL_1234567890"
    b64_str = base64.b64encode(bad_data).decode()
    resp = {
        'status': 'completed',
        'steps': [{
            'type': 'model_output',
            'content': [{
                'type': 'audio',
                'mime_type': 'audio/wav',
                'data': b64_str
            }]
        }]
    }
    with pytest.raises(ProviderError) as exc:
        decode_audio(resp, dest)
    assert exc.value.kind == ErrorKind.SCHEMA
    assert exc.value.error_type == 'decode_error'
    assert "invalid RIFF" in exc.value.validation_reason
    assert not dest.exists()
    assert b64_str not in str(exc.value)


def test_canonical_decoder_missing_audio(tmp_path):
    dest = tmp_path / "missing.wav"
    resp = {
        'status': 'completed',
        'steps': [{
            'type': 'model_output',
            'content': [{
                'type': 'text',
                'text': 'Secret transcript only, no audio.'
            }]
        }]
    }
    with pytest.raises(ProviderError) as exc:
        decode_audio(resp, dest)
    assert exc.value.kind == ErrorKind.SCHEMA
    assert exc.value.error_type == 'decode_error'
    assert "no audio" in exc.value.validation_reason or "missing audio" in exc.value.validation_reason
    assert not dest.exists()
    assert 'Secret transcript' not in str(exc.value)


def test_canonical_decoder_text_and_audio(tmp_path):
    dest = tmp_path / "text_audio.wav"
    raw_wav = wav_bytes()
    resp = {
        'status': 'completed',
        'steps': [{
            'type': 'model_output',
            'content': [
                {'type': 'text', 'text': 'Confidential speech script text.'},
                {'type': 'audio', 'mime_type': 'audio/wav', 'data': base64.b64encode(raw_wav).decode()},
                {'type': 'text', 'text': 'Subsequent commentary.'}
            ]
        }]
    }
    payload = decode_audio(resp, dest)
    assert isinstance(payload, AudioPayload)
    assert payload.bytes == raw_wav
    assert dest.read_bytes() == raw_wav


def test_canonical_decoder_audio_in_subsequent_step(tmp_path):
    dest = tmp_path / "subsequent_step.wav"
    raw_wav = wav_bytes()
    resp = {
        'status': 'completed',
        'steps': [
            {'type': 'thought', 'content': [{'type': 'text', 'text': 'Internal reasoning without audio'}]},
            {'type': 'model_output', 'content': [{'type': 'audio', 'mime_type': 'audio/wav', 'data': base64.b64encode(raw_wav).decode()}]}
        ]
    }
    payload = decode_audio(resp, dest)
    assert isinstance(payload, AudioPayload)
    assert payload.bytes == raw_wav
    assert dest.read_bytes() == raw_wav


def test_canonical_decoder_multiple_audio_blocks(tmp_path):
    dest = tmp_path / "multiple_audio.wav"
    raw_wav = wav_bytes()
    b64_wav = base64.b64encode(raw_wav).decode()
    # Case A: across steps
    resp_steps = {
        'status': 'completed',
        'steps': [
            {'type': 'model_output', 'content': [{'type': 'audio', 'mime_type': 'audio/wav', 'data': b64_wav}]},
            {'type': 'model_output', 'content': [{'type': 'audio', 'mime_type': 'audio/wav', 'data': b64_wav}]}
        ]
    }
    with pytest.raises(ProviderError) as exc:
        decode_audio(resp_steps, dest)
    assert exc.value.kind == ErrorKind.SCHEMA
    assert exc.value.error_type == 'decode_error'
    assert "multiple audio blocks" in exc.value.validation_reason
    assert not dest.exists()
    assert b64_wav not in str(exc.value)

    # Case B: within same step
    resp_same_step = {
        'status': 'completed',
        'steps': [{
            'type': 'model_output',
            'content': [
                {'type': 'audio', 'mime_type': 'audio/wav', 'data': b64_wav},
                {'type': 'audio', 'mime_type': 'audio/wav', 'data': b64_wav}
            ]
        }]
    }
    with pytest.raises(ProviderError) as exc2:
        decode_audio(resp_same_step, dest)
    assert exc2.value.kind == ErrorKind.SCHEMA
    assert exc2.value.error_type == 'decode_error'
    assert "multiple audio blocks" in exc2.value.validation_reason


def test_canonical_decoder_invalid_base64(tmp_path):
    dest = tmp_path / "invalid_b64.wav"
    bad_b64 = "this_is_not_valid_base64_data!!!!"
    resp = {
        'status': 'completed',
        'steps': [{
            'type': 'model_output',
            'content': [{
                'type': 'audio',
                'mime_type': 'audio/wav',
                'data': bad_b64
            }]
        }]
    }
    with pytest.raises(ProviderError) as exc:
        decode_audio(resp, dest)
    assert exc.value.kind == ErrorKind.SCHEMA
    assert exc.value.error_type == 'decode_error'
    assert "invalid base64" in exc.value.validation_reason
    assert not dest.exists()
    assert bad_b64 not in str(exc.value)


@pytest.mark.parametrize('bad_param', ['sample_rate', 'channels', 'empty', 'truncated', 'unsupported_mime'])
def test_canonical_decoder_format_and_mime_boundaries(tmp_path, bad_param):
    dest = tmp_path / "bad_format.wav"
    out = io.BytesIO()
    if bad_param == 'sample_rate':
        with wave.open(out, 'wb') as wav:
            wav.setparams((1, 2, 48000, 0, 'NONE', 'not compressed'))
            wav.writeframes(struct.pack('<hh', 1000, -1000) * 2400)
        data = out.getvalue()
        mime = 'audio/wav'
    elif bad_param == 'channels':
        with wave.open(out, 'wb') as wav:
            wav.setparams((2, 2, 24000, 0, 'NONE', 'not compressed'))
            wav.writeframes(struct.pack('<hhhh', 1000, -1000, 1000, -1000) * 2400)
        data = out.getvalue()
        mime = 'audio/wav'
    elif bad_param == 'empty':
        with wave.open(out, 'wb') as wav:
            wav.setparams((1, 2, 24000, 0, 'NONE', 'not compressed'))
        data = out.getvalue()
        mime = 'audio/wav'
    elif bad_param == 'truncated':
        full_wav = wav_bytes()
        data = full_wav[:len(full_wav) - 50]
        mime = 'audio/wav'
    elif bad_param == 'unsupported_mime':
        data = wav_bytes()
        mime = 'video/mp4'

    resp = {
        'status': 'completed',
        'steps': [{
            'type': 'model_output',
            'content': [{
                'type': 'audio',
                'mime_type': mime,
                'data': base64.b64encode(data).decode()
            }]
        }]
    }
    with pytest.raises(ProviderError) as exc:
        decode_audio(resp, dest)
    assert exc.value.kind == ErrorKind.SCHEMA
    assert exc.value.error_type == 'decode_error'
    assert not dest.exists()


@pytest.mark.parametrize('value', [False, 1, 'true', None])
def test_cloud_consent_is_required_and_strict(value):
    with pytest.raises(ValidationError): CloudTTSConfig(send_text_to_cloud=value)


def test_config_and_cache_boundaries(monkeypatch):
    initial = GeminiTTSProvider(spec()).cache_key
    for values in ({'host_voice': 'Leda'}, {'guest_voice': 'Charon'}, {'style_instruction': '轻松'}):
        assert GeminiTTSProvider(spec(**values)).cache_key != initial
    changed = spec(); changed.model = 'future-tts'
    assert GeminiTTSProvider(changed).cache_key != initial
    monkeypatch.setenv('GEMINI_API_KEY', 'different-secret')
    assert GeminiTTSProvider(spec()).cache_key == initial
    data = spec().model_dump(); data['base_url'] = 'https://other.example'
    with pytest.raises(ValidationError): ProviderSpec.model_validate(data)
    with pytest.raises(ValidationError): spec(host_voice='Puck')
    with pytest.raises(ValidationError): spec(style_instruction='x'*513)
    with pytest.raises(ValidationError): CloudTTSConfig.model_validate({'send_text_to_cloud': True, 'api_key': 'no'})


def test_segmentation_preserves_text_roles_topic_and_question_answer():
    script = PodcastScript(chapter_id='0001', title='主题', source_locator='test', turns=[
        DialogueTurn(speaker='主持人', text='长句中文😀'*250+'。'),
        DialogueTurn(speaker='嘉宾', text='为什么？'), DialogueTurn(speaker='主持人', text='因为协作有成本。')])
    parts = list(speech_segments(script))
    assert ''.join(t.text for _, p in parts for t in p.turns) == ''.join(t.text for t in script.turns)
    assert all(sum(len(t.text) for t in p.turns) <= 600 and len(p.turns) <= 24 for _, p in parts)
    assert [t.speaker for t in parts[-1][1].turns][-2:] == ['嘉宾', '主持人']
    assert list(speech_segments(script)) == parts


class SegmentFake(MockTTSProvider):
    def __init__(self, name='A', fail_at=None, kind=ErrorKind.QUOTA, key='v1'):
        self.name, self.fail_at, self.kind, self.cache_key = name, fail_at, kind, key
        self.calls = []

    def capabilities(self):
        return ProviderCapabilities(speech=True, speech_segments=True, multi_speaker=True, cloud=True)

    def synthesize_segment(self, part, destination):
        self.calls.append(part)
        if len(self.calls) == self.fail_at: raise ProviderError(self.kind)
        destination.write_bytes(wav_bytes())
        return SegmentSpeechInfo(voices={t.speaker: 'fake-'+t.speaker for t in part.turns})


def snap(root):
    return {str(p.relative_to(root)): (sha256_file(p), p.stat().st_mtime_ns)
            for p in root.rglob('*') if p.is_file() and p.name != '.lock'}


def test_segment_failover_resume_and_voice_change_preserve_llm(tmp_path):
    a, b = SegmentFake(fail_at=2), SegmentFake('B')
    pipe = Pipeline(MockLLMProvider(), ProviderChain([a, b]), tmp_path)
    root = pipe.generate(DEMO, minutes=2)
    assert len(a.calls) == 2 and b.calls[0] == a.calls[-1]
    before = snap(root); calls = load_manifest(root/'manifest.json').ai_calls
    pipe.resume_job(root)
    assert snap(root) == before
    assert {c.provider for c in calls if c.kind == 'tts' and c.status == 'completed'} == {'A', 'B'}
    changed = SegmentFake('B', key='voice2')
    Pipeline(MockLLMProvider(), ProviderChain([SegmentFake(), changed]), tmp_path).resume_job(root)
    after = load_manifest(root/'manifest.json')
    assert len(changed.calls) == len([c for c in calls if c.kind == 'tts' and c.provider == 'B' and c.status == 'completed'])
    assert [c for c in after.ai_calls if c.kind == 'llm'] == [c for c in calls if c.kind == 'llm']


def test_quota_at_segment_six_preserves_first_five(tmp_path):
    from bookcast.models import Manifest
    from bookcast.pipeline import _Runner
    from bookcast.speech_segments import render_segments
    a = SegmentFake(fail_at=6)
    llm = ProviderChain([MockLLMProvider()])
    script = PodcastScript(chapter_id='0001', title='分段', source_locator='self-authored',
        turns=[DialogueTurn(speaker='主持人' if i % 2 == 0 else '嘉宾', text=str(i) + '文' * 599) for i in range(8)])
    manifest = Manifest(book_id='test', source_sha256='a'*64, source_name='test.txt', source_format='txt', config={})
    runner = _Runner(tmp_path, manifest, llm, ProviderChain([a]))
    with pytest.raises(ProviderError):
        render_segments(runner, script)
    first = snap(tmp_path / 'audio/segments')
    a.fail_at = None
    recovered = _Runner(tmp_path, load_manifest(tmp_path/'manifest.json'), llm, ProviderChain([a]))
    render_segments(recovered, script)
    assert len(a.calls) == 9  # six before failure, only three missing chunks on resume
    assert all(snap(tmp_path/'audio/segments')[name] == value for name, value in first.items())
    successful = [c for c in recovered.manifest.ai_calls if c.status == 'completed']
    assert [c.provider for c in successful] == ['A']*8


def test_corrupt_segment_repairs_only_that_segment(tmp_path):
    provider = SegmentFake()
    pipe = Pipeline(MockLLMProvider(), provider, tmp_path)
    root = pipe.generate(DEMO, minutes=2)
    calls = load_manifest(root/'manifest.json').ai_calls
    previous = snap(root/'audio/segments')
    (root/'audio/segments/0002-0001.wav').write_bytes(b'broken')
    count = len(provider.calls)
    pipe.resume_job(root)
    assert len(provider.calls) == count + 1
    assert [c for c in load_manifest(root/'manifest.json').ai_calls if c.kind == 'llm'] == [c for c in calls if c.kind == 'llm']
    assert previous['0001-0001.wav'] == snap(root/'audio/segments')['0001-0001.wav']


@pytest.mark.parametrize('kind', [ErrorKind.AUTH, ErrorKind.PERMISSION, ErrorKind.INPUT, ErrorKind.SCHEMA])
def test_permanent_errors_never_switch_provider(tmp_path, kind):
    backup = SegmentFake('B')
    with pytest.raises(BookCastError):
        Pipeline(MockLLMProvider(), ProviderChain([SegmentFake(fail_at=2, kind=kind), backup]), tmp_path).generate(DEMO, minutes=2)
    assert not backup.calls


def test_no_mock_or_unit_cloud_chain_and_provider_change_resynthesizes(tmp_path):
    from test_tts import UnitFake
    for backup in (MockTTSProvider(), UnitFake()):
        with pytest.raises(BookCastError):
            Pipeline(MockLLMProvider(), ProviderChain([SegmentFake(), backup]), tmp_path)
    for provider, subdir in ((MockTTSProvider(), 'mock'),):
        root = Pipeline(MockLLMProvider(), provider, tmp_path/subdir).generate(DEMO, minutes=1)
        audio = snap(root/'audio'); new = SegmentFake()
        Pipeline(MockLLMProvider(), new, tmp_path/subdir).resume_job(root)
        assert new.calls and snap(root/'audio') != audio
    root = Pipeline(MockLLMProvider(), UnitFake(), tmp_path/'units').generate(DEMO, minutes=1)
    with pytest.raises(BookCastError):
        Pipeline(MockLLMProvider(), SegmentFake(), tmp_path/'units').resume_job(root)


KILL = r'''
import sys,time
from pathlib import Path
sys.path.insert(0,str(Path.cwd()/'tests'))
from test_gemini_tts import SegmentFake, DEMO
from bookcast.providers import MockLLMProvider
from bookcast.pipeline import Pipeline
import bookcast.pipeline as core
output,ready,window=sys.argv[1:]
original=core._Runner.observe
def observe(self, attempt):
    original(self, attempt)
    if attempt.task=='tts_segment:0002:0001' and attempt.status==window:
        Path(ready).write_text('ready')
        while True: time.sleep(.05)
core._Runner.observe=observe
Pipeline(MockLLMProvider(),SegmentFake(),Path(output)).generate(DEMO,minutes=2)
'''


@pytest.mark.parametrize('window', ['running', 'completed'])
def test_real_sigkill_keeps_completed_segments(tmp_path, window):
    output, ready = tmp_path/'out', tmp_path/'ready'
    process = subprocess.Popen([sys.executable, '-c', KILL, str(output), str(ready), window],
                               stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        deadline = time.monotonic()+30
        while not ready.exists() and process.poll() is None and time.monotonic() < deadline: time.sleep(.02)
        assert ready.exists()
        root = next(output.iterdir()); first = root/'audio/segments/0001-0001.wav'
        before = sha256_file(first), first.stat().st_mtime_ns
        process.kill(); process.wait(timeout=10)
        Pipeline(MockLLMProvider(), SegmentFake(), output).resume_job(root)
        assert (sha256_file(first), first.stat().st_mtime_ns) == before
        m = load_manifest(root/'manifest.json')
        calls = [c for c in m.ai_calls if c.task == 'tts_segment:0002:0001']
        assert len(calls) == (2 if window == 'running' else 1) and m.status == 'completed'
    finally:
        if process.poll() is None: process.kill()
        process.wait(timeout=10); process.stderr.close()
import os
def test_speaker_mapping_and_metadata(monkeypatch, tmp_path):
    monkeypatch.setenv('GEMINI_API_KEY', 'fake-key')
    sent = []

    def opening(req, timeout):
        sent.append(req)
        return io.BytesIO(json.dumps(response()).encode())

    monkeypatch.setattr('bookcast.adapters.gemini.request.build_opener',
                        lambda *args: SimpleNamespace(open=opening))
    provider = GeminiTTSProvider(spec(host_voice='HostA', guest_voice='GuestB', mode='conversational'))
    part = SpeechSegment(turns=[SpeechTurn(speaker='主持人', text='你好'),
                                SpeechTurn(speaker='嘉宾', text='(笑声) 我很好！')])
    dest = tmp_path/'out.wav'
    info = provider.synthesize_segment(part, dest)

    assert len(sent) == 1
    assert sent[0].full_url == 'https://generativelanguage.googleapis.com/v1beta/interactions'
    payload = json.loads(sent[0].data)
    content = payload['input'][0]['content']
    assert [item['text'] for item in content] == ['你好', '我很好！']
    assert [item['annotations'][0]['speaker'] for item in content] == ['Host', 'Guest']
    assert 'calm' in content[0]['annotations'][0]['style'].lower()
    assert 'natural' in content[1]['annotations'][0]['style']
    assert payload['generation_config']['speech_config']['mode'] == 'conversational'
    assert [item['voice'] for item in payload['generation_config']['speech_config']['speakers']] == ['HostA', 'GuestB']
    assert dest.read_bytes() == wav_bytes()
    assert info.voices == {'主持人': 'HostA', '嘉宾': 'GuestB'}


def test_classify_http_extracts_official_retry_delays_and_identifies_daily_quota():
    from bookcast.adapters.gemini import classify_http
    
    # 1. Official header Retry-After
    err1 = classify_http(429, b'{}', {'Retry-After': '36'})
    assert err1.kind == ErrorKind.RATE_LIMIT
    assert err1.retry_after == 36.0
    assert err1.quota_reason is None

    # 2. Official message with retry in Xs
    msg_body = json.dumps({'error': {'message': 'Please retry in 42s or upgrade at https://example.com'}}).encode()
    err2 = classify_http(429, msg_body, {})
    assert err2.kind == ErrorKind.RATE_LIMIT
    assert err2.retry_after == 42.0

    # 3. Official details with RetryInfo retryDelay
    details_body = json.dumps({'error': {'details': [{'@type': 'type.googleapis.com/google.rpc.RetryInfo', 'retryDelay': '28s'}]}}).encode()
    err3 = classify_http(429, details_body, {})
    assert err3.kind == ErrorKind.RATE_LIMIT
    assert err3.retry_after == 28.0

    # 4. Daily limit in message
    daily_msg_body = json.dumps({'error': {'message': 'Rate limit exceeded for model gemini-3.8-flash-tts (limit: 10 requests per day on Free Tier). Please retry in 58s.'}}).encode()
    err4 = classify_http(429, daily_msg_body, {'Retry-After': '58'})
    assert err4.kind == ErrorKind.QUOTA
    assert err4.quota_reason == 'DAILY_LIMIT'
    assert err4.retry_after == 58.0

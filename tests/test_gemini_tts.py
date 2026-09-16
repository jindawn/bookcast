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

from bookcast.adapters.gemini import GeminiTTSProvider, classify_http, decode_pcm, NoRedirect
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


def response():
    return {'modelVersion': 'gemini-3.1-flash-tts-preview',
            'usageMetadata': {'promptTokenCount': 12, 'candidatesTokenCount': 25},
            'candidates': [{'finishReason': 'STOP', 'content': {'parts': [{'inlineData': {
                'mimeType': 'audio/L16;codec=pcm;rate=24000',
                'data': base64.b64encode(struct.pack('<hh', 1000, -1000) * 2400).decode()}}]}}]}


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
    assert req.full_url == 'https://generativelanguage.googleapis.com/v1beta/models/gemini-3.1-flash-tts-preview:generateContent'
    assert req.headers['X-goog-api-key'] == 'fake-secret-test-only'
    assert 'fake-secret' not in str(payload) + provider.cache_key + str(info) + capsys.readouterr().err
    configs = payload['generationConfig']['speechConfig']['multiSpeakerVoiceConfig']['speakerVoiceConfigs']
    assert [c['speaker'] for c in configs] == ['HostA', 'HostB']
    assert 'HostA: 分工有收益。\nHostB: 那沟通成本呢？' in payload['contents'][0]['parts'][0]['text']
    assert provider.last_usage.input_tokens == 12 and provider.last_usage.reasoning_tokens is None
    provider.synthesize_segment(SpeechSegment(turns=[segment().turns[0]]), target)
    assert 'voiceConfig' in json.loads(sent[1].data)['generationConfig']['speechConfig']


@pytest.mark.parametrize('status,body,kind', [
    (400, {'error': {'details': [{'reason': 'API_KEY_INVALID'}]}}, ErrorKind.AUTH),
    (401, {}, ErrorKind.AUTH), (403, {}, ErrorKind.PERMISSION), (402, {}, ErrorKind.QUOTA),
    (429, {'error': {'details': [{'violations': [{'quotaId': 'RequestsPerDayPerProject'}]}]}}, ErrorKind.QUOTA),
    (429, {'error': {'details': [{'violations': [{'quotaValue': '0'}]}]}}, ErrorKind.QUOTA),
    (429, {'error': {'details': [{'violations': [{'quotaId': 'RequestsPerMinutePerProject'}]}]}}, ErrorKind.RATE_LIMIT),
    (429, {}, ErrorKind.RATE_LIMIT), (408, {}, ErrorKind.TIMEOUT), (504, {}, ErrorKind.TIMEOUT),
    (500, {}, ErrorKind.UNAVAILABLE), (503, {}, ErrorKind.UNAVAILABLE),
    (400, {}, ErrorKind.INPUT), (404, {}, ErrorKind.INPUT),
    (400, {'error': {'details': [{'reason': 'BILLING_DISABLED'}]}}, ErrorKind.QUOTA),
    (400, {'error': ['not-object']}, ErrorKind.INPUT),
])
def test_safe_error_classification(status, body, kind):
    failure = classify_http(status, json.dumps(body).encode())
    assert failure.kind == kind
    assert failure.retryable == (kind in {ErrorKind.QUOTA, ErrorKind.RATE_LIMIT, ErrorKind.TIMEOUT, ErrorKind.UNAVAILABLE})


@pytest.mark.parametrize('mode', ['timeout', 'network', 'http', 'json', 'missing-key', 'redirect'])
def test_request_failures_are_safe_and_never_retry(monkeypatch, mode):
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
    if mode == 'missing-key': monkeypatch.delenv('GEMINI_API_KEY')
    with pytest.raises(ProviderError) as exc: GeminiTTSProvider(spec())._request({})
    assert 'SECRET' not in str(exc.value)
    assert len(calls) == (0 if mode == 'missing-key' else 1)


@pytest.mark.parametrize('mode', ['empty', 'odd', 'silent', 'base64', 'mime', 'rate', 'channels', 'truncated', 'parts'])
def test_invalid_audio_is_permanent(mode):
    data = response(); part = data['candidates'][0]['content']['parts'][0]['inlineData']
    if mode in {'empty', 'odd', 'silent'}:
        part['data'] = base64.b64encode({'empty': b'', 'odd': b'x', 'silent': b'\0\0'}[mode]).decode()
    if mode == 'base64': part['data'] = '###'
    if mode == 'mime': part['mimeType'] = 'audio/mp3'
    if mode == 'rate': part['mimeType'] = 'audio/L16;rate=48000'
    if mode == 'channels': part['mimeType'] = 'audio/L16;rate=24000;channels=2'
    if mode == 'truncated': data['candidates'][0]['finishReason'] = 'MAX_TOKENS'
    if mode == 'parts': data['candidates'][0]['content']['parts'] *= 2
    with pytest.raises(ProviderError) as exc: decode_pcm(data)
    assert exc.value.kind == ErrorKind.SCHEMA


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
        pcm = decode_pcm(response())
        with wave.open(str(destination), 'wb') as output:
            output.setparams((1, 2, 24000, 0, 'NONE', 'not compressed')); output.writeframes(pcm)
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
    b = SegmentFake('B')
    recovered = _Runner(tmp_path, load_manifest(tmp_path/'manifest.json'), llm, ProviderChain([b]))
    render_segments(recovered, script)
    assert len(b.calls) == 3
    assert all(snap(tmp_path/'audio/segments')[name] == value for name, value in first.items())
    successful = [c for c in recovered.manifest.ai_calls if c.status == 'completed']
    assert [c.provider for c in successful] == ['A']*5 + ['B']*3


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


def test_no_mock_or_unit_cloud_chain_and_completed_legacy_retained(tmp_path):
    from test_tts import UnitFake
    for backup in (MockTTSProvider(), UnitFake()):
        with pytest.raises(BookCastError):
            Pipeline(MockLLMProvider(), ProviderChain([SegmentFake(), backup]), tmp_path)
    for provider, subdir in ((MockTTSProvider(), 'mock'), (UnitFake(), 'units')):
        root = Pipeline(MockLLMProvider(), provider, tmp_path/subdir).generate(DEMO, minutes=1)
        audio = snap(root/'audio'); new = SegmentFake()
        Pipeline(MockLLMProvider(), new, tmp_path/subdir).resume_job(root)
        assert not new.calls and snap(root/'audio') == audio


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

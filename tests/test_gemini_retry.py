"""Offline Gemini time, retry, throttle and checkpoint regression tests."""

import io
import json
import socket
import subprocess
import sys
from types import SimpleNamespace
from urllib.error import HTTPError

import pytest

from bookcast.adapters.gemini import GeminiTTSProvider
from bookcast.models import DialogueTurn, Manifest, PodcastScript
from bookcast.pipeline import _Runner, load_manifest
from bookcast.provider_api import ErrorKind, ProviderError
from bookcast.provider_chain import ProviderChain
from bookcast.provider_config import CloudTTSConfig, ProviderSpec
from bookcast.providers import MockLLMProvider
from bookcast.speech_segments import render_segments
from test_gemini_tts import response


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class FakeSleeper:
    def __init__(self, clock):
        self.clock = clock
        self.calls = []

    def __call__(self, seconds):
        self.calls.append(seconds)
        self.clock.now += seconds


@pytest.fixture
def setup(monkeypatch):
    monkeypatch.setenv('GEMINI_API_KEY', 'offline-test-key')
    monkeypatch.setattr(GeminiTTSProvider, '_last_request_time', 0.0)
    clock = FakeClock()
    sleeper = FakeSleeper(clock)
    spec = ProviderSpec(name='gemini', kind='tts', type='gemini-tts', model='gemini-test',
                        api_key_env='GEMINI_API_KEY', cloud_tts=CloudTTSConfig(send_text_to_cloud=True))
    return GeminiTTSProvider(spec, clock=clock, sleeper=sleeper), clock, sleeper


def http_error(status, body, headers=None):
    return HTTPError('https://generativelanguage.googleapis.com/v1beta/interactions', status,
                     'test error', headers or {}, io.BytesIO(json.dumps(body).encode()))


def install_transport(monkeypatch, opening):
    monkeypatch.setattr('bookcast.adapters.gemini.request.build_opener',
                        lambda *args: SimpleNamespace(open=opening))


def test_offline_network_guard_fails_immediately():
    with pytest.raises(AssertionError, match='Offline test attempted real network access'):
        socket.create_connection(('generativelanguage.googleapis.com', 443), timeout=0.01)

    child = subprocess.run([sys.executable, '-c',
                            "import socket; socket.create_connection(('api.deepseek.com', 443), timeout=0.01)"],
                           capture_output=True, text=True, check=False)
    assert child.returncode != 0
    assert 'Offline test attempted real network access' in child.stderr


def test_rate_limit_exceeded_backoff(monkeypatch, setup):
    provider, clock, sleeper = setup
    monkeypatch.setattr('bookcast.adapters.gemini.random.uniform', lambda a, b: 0)
    requests = []

    def opening(req, timeout):
        requests.append(req)
        raise http_error(429, {'error': {'status': 'RESOURCE_EXHAUSTED',
                                        'message': 'too_many_requests... rate limit exceeded'}})

    install_transport(monkeypatch, opening)
    with pytest.raises(ProviderError) as exc:
        provider._request({'model': 'gemini-test'})
    assert exc.value.kind == ErrorKind.RATE_LIMIT
    assert exc.value.error_type == 'too_many_requests'
    assert len(requests) == 5
    assert sleeper.calls == [10, 20, 40, 60]
    assert clock.now == 1130


def test_quota_exceeded_no_retry(monkeypatch, setup):
    provider, _, sleeper = setup
    requests = []

    def opening(req, timeout):
        requests.append(req)
        raise http_error(429, {'error': {'status': 'RESOURCE_EXHAUSTED',
                          'message': 'Quota exceeded.', 'details': [{'violations': [{'quotaId': 'PerDay'}]}]}})

    install_transport(monkeypatch, opening)
    with pytest.raises(ProviderError) as exc:
        provider._request({'model': 'gemini-test'})
    assert exc.value.kind == ErrorKind.QUOTA
    assert len(requests) == 1
    assert sleeper.calls == []


def test_retry_after_header(monkeypatch, setup):
    provider, _, sleeper = setup
    requests = []

    def opening(req, timeout):
        requests.append(req)
        if len(requests) == 1:
            raise http_error(429, {'error': {'status': 'RESOURCE_EXHAUSTED', 'message': 'rate limit'}},
                             {'Retry-After': '5'})
        return io.BytesIO(json.dumps(response()).encode())

    install_transport(monkeypatch, opening)
    assert provider._request({'model': 'gemini-test'})['status'] == 'completed'
    assert len(requests) == 2
    assert sleeper.calls == [5]


def test_rpm_throttle_uses_fake_time_without_changing_production_interval(monkeypatch, setup):
    provider, clock, sleeper = setup
    requests = []

    def opening(req, timeout):
        requests.append(req)
        return io.BytesIO(json.dumps(response()).encode())

    install_transport(monkeypatch, opening)
    assert provider.settings.min_request_interval == 25
    provider._request({'model': 'gemini-test'})
    provider._request({'model': 'gemini-test'})
    assert len(requests) == 2
    assert sleeper.calls == [25]
    assert clock.now == 1025


def test_success_chunk_no_repeat(monkeypatch, setup, tmp_path):
    provider, _, sleeper = setup
    requests = []
    fail_second = True

    def opening(req, timeout):
        nonlocal fail_second
        text = json.loads(req.data)['input'][0]['content'][0]['text']
        requests.append(text)
        if text.startswith('second-') and fail_second:
            fail_second = False
            raise http_error(403, {'error': {'status': 'PERMISSION_DENIED'}})
        return io.BytesIO(json.dumps(response()).encode())

    install_transport(monkeypatch, opening)
    script = PodcastScript(chapter_id='0001', title='resume', source_locator='self-authored', turns=[
        DialogueTurn(speaker='主持人', text='first-' + '甲' * 594),
        DialogueTurn(speaker='嘉宾', text='second-' + '乙' * 593)])
    llm = ProviderChain([MockLLMProvider()])
    tts = ProviderChain([provider])
    manifest = Manifest(book_id='offline', source_sha256='a' * 64,
                        source_name='offline.txt', source_format='txt', config={})
    runner = _Runner(tmp_path, manifest, llm, tts)
    with pytest.raises(ProviderError) as exc:
        render_segments(runner, script)
    assert exc.value.kind == ErrorKind.PERMISSION
    assert [text[:7] for text in requests] == ['first-甲', 'second-']
    checkpoint = load_manifest(tmp_path/'manifest.json')
    assert checkpoint.steps['tts_segment:0001:0001'].status == 'completed'
    assert (tmp_path/'audio/segments/0001-0001.wav').is_file()
    assert not (tmp_path/'audio/segments/0001-0002.wav').exists()

    before = len(requests)
    resumed = _Runner(tmp_path, checkpoint, llm, tts)
    render_segments(resumed, script)
    assert len(requests) == before + 1
    assert [text[:7] for text in requests[before:]] == ['second-']
    assert [task for task in resumed.manifest.steps if task.startswith('tts_segment:')] == [
        'tts_segment:0001:0001', 'tts_segment:0001:0002']
    assert all(resumed.manifest.steps[task].status == 'completed' for task in (
        'tts_segment:0001:0001', 'tts_segment:0001:0002'))
    assert sleeper.calls == [25, 25]

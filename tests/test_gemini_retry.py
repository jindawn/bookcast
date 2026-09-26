"""Offline Gemini time, retry, throttle and checkpoint regression tests."""

import io
import json
import socket
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from email.utils import format_datetime
from types import SimpleNamespace
from urllib.error import HTTPError

import pytest

from bookcast.adapters.gemini import GeminiRequestLimiter, GeminiTTSProvider
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
def setup(monkeypatch, tmp_path):
    monkeypatch.setenv('GEMINI_API_KEY', 'offline-test-key')
    clock = FakeClock()
    sleeper = FakeSleeper(clock)
    spec = ProviderSpec(name='gemini', kind='tts', type='gemini-tts', model='gemini-test',
                        api_key_env='GEMINI_API_KEY', cloud_tts=CloudTTSConfig(send_text_to_cloud=True))
    limiter = GeminiRequestLimiter(tmp_path / 'retry-slots.sqlite3', clock=clock)
    return GeminiTTSProvider(spec, clock=clock, sleeper=sleeper, limiter=limiter), clock, sleeper


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
    assert exc.value.error_type == 'RATE_LIMIT'
    assert len(requests) == 5
    assert sleeper.calls == [25, 25, 40, 60]
    assert clock.now == 1150


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
    assert sleeper.calls == [25]


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
    manifest = Manifest(job_id='job-resume', output_id='output-resume',
                        book_id='offline', source_sha256='a' * 64,
                        source_name='offline.txt', source_format='txt', config={})
    runner = _Runner(tmp_path, manifest, llm, tts)
    with pytest.raises(ProviderError) as exc:
        render_segments(runner, script)
    assert exc.value.kind == ErrorKind.PERMISSION, exc.value.error_type
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
    physical = [json.loads(line) for line in (tmp_path/'usage/physical_requests.jsonl').read_text().splitlines()]
    assert [(row['job_id'], row['output_id'], row['logical_chunk_id'], row['physical_attempt_index'])
            for row in physical] == [
                ('job-resume', 'output-resume', 'tts_segment:0001:0001', 0),
                ('job-resume', 'output-resume', 'tts_segment:0001:0002', 0),
                ('job-resume', 'output-resume', 'tts_segment:0001:0002', 0),
            ]


def test_shared_slots_same_instance_and_independent_instances(tmp_path):
    clock = FakeClock()
    path = tmp_path / 'shared.sqlite3'
    first = GeminiRequestLimiter(path, clock=clock)
    second = GeminiRequestLimiter(path, clock=clock)
    slots = [first.reserve_next_slot(25), first.reserve_next_slot(25),
             second.reserve_next_slot(25)]
    assert slots == [1000, 1025, 1050]


def test_two_provider_instances_share_physical_send_spacing(monkeypatch, setup, tmp_path):
    first, clock, sleeper = setup
    second = GeminiTTSProvider(first.spec, clock=clock, sleeper=sleeper,
        limiter=GeminiRequestLimiter(tmp_path / 'retry-slots.sqlite3', clock=clock))
    sent = []

    def opening(req, timeout):
        sent.append(clock.now)
        return io.BytesIO(json.dumps(response()).encode())

    install_transport(monkeypatch, opening)
    first._request({'model': 'gemini-test'})
    second._request({'model': 'gemini-test'})
    assert sent == [1000, 1025] and sleeper.calls == [25]


def test_configured_short_interval_cannot_break_three_rpm(monkeypatch, setup):
    provider, clock, sleeper = setup
    provider.settings = provider.settings.model_copy(update={'min_request_interval': 1})
    sent = []

    def opening(req, timeout):
        sent.append(clock.now)
        return io.BytesIO(json.dumps(response()).encode())

    install_transport(monkeypatch, opening)
    provider._request({'model': 'gemini-test'})
    provider._request({'model': 'gemini-test'})
    assert sent == [1000, 1025] and sleeper.calls == [25]


def test_shared_slots_two_threads(tmp_path):
    clock = FakeClock()
    path = tmp_path / 'shared.sqlite3'
    limiters = [GeminiRequestLimiter(path, clock=clock) for _ in range(2)]
    with ThreadPoolExecutor(max_workers=2) as pool:
        slots = list(pool.map(lambda limiter: limiter.reserve_next_slot(25), limiters))
    assert sorted(slots) == [1000, 1025]


def test_crash_after_reservation_recovers(tmp_path):
    clock = FakeClock()
    path = tmp_path / 'shared.sqlite3'
    assert GeminiRequestLimiter(path, clock=clock).reserve_next_slot(25) == 1000
    # The reserving process is gone. The new process waits one finite slot.
    assert GeminiRequestLimiter(path, clock=clock).reserve_next_slot(25) == 1025
    clock.now = 1100
    assert GeminiRequestLimiter(path, clock=clock).reserve_next_slot(25) == 1100


def test_crash_during_long_retry_after_does_not_reserve_distant_slot(monkeypatch, setup):
    provider, clock, sleeper = setup
    calls = []

    def opening(req, timeout):
        calls.append(clock.now)
        raise http_error(429, {'error': {'status': 'RESOURCE_EXHAUSTED'}},
                         {'Retry-After': '3600'})

    def crash_on_wait(seconds):
        assert seconds == 3600
        raise KeyboardInterrupt

    install_transport(monkeypatch, opening)
    provider.sleeper = crash_on_wait
    with pytest.raises(KeyboardInterrupt):
        provider._request({'model': 'gemini-test'})
    survivor = GeminiRequestLimiter(provider.limiter.path, clock=clock)
    assert calls == [1000]
    assert survivor.reserve_next_slot(25) == 1025


def test_delayed_worker_cannot_crowd_another_send(tmp_path):
    clock = FakeClock()
    path = tmp_path / 'shared.sqlite3'
    first = GeminiRequestLimiter(path, clock=clock)
    second = GeminiRequestLimiter(path, clock=clock)
    assert first.reserve_next_slot(25) == 1000
    assert second.reserve_next_slot(25) == 1025
    # First worker was descheduled. The later worker sends at its reservation.
    clock.now = 1025
    assert second.claim_send_slot(25) == 1025
    clock.now = 1026
    assert first.claim_send_slot(25) == 1050


@pytest.mark.parametrize('elapsed,retry_after,expected_wait', [(5, '5', 20), (15, '60', 60)])
def test_retry_deadline_is_max_of_limiter_and_retry_after(monkeypatch, setup,
                                                            elapsed, retry_after, expected_wait):
    provider, clock, sleeper = setup
    seen = []

    def opening(req, timeout):
        seen.append(clock.now)
        if len(seen) == 1:
            clock.now += elapsed
            raise http_error(429, {'error': {'status': 'RESOURCE_EXHAUSTED'}},
                             {'Retry-After': retry_after})
        return io.BytesIO(json.dumps(response()).encode())

    install_transport(monkeypatch, opening)
    provider._request({'model': 'gemini-test'})
    assert len(seen) == 2 and sleeper.calls == [expected_wait]
    assert seen[1] - seen[0] == elapsed + expected_wait


def test_backoff_deadline_wins(monkeypatch, setup):
    provider, clock, sleeper = setup
    monkeypatch.setattr('bookcast.adapters.gemini.random.uniform', lambda a, b: 0)
    seen = []

    def opening(req, timeout):
        seen.append(clock.now)
        if len(seen) == 1:
            clock.now += 20  # limiter has 5s left; backoff has 10s
            raise http_error(503, {'error': {'status': 'UNAVAILABLE'}})
        return io.BytesIO(json.dumps(response()).encode())

    install_transport(monkeypatch, opening)
    provider._request({'model': 'gemini-test'})
    assert sleeper.calls == [10] and seen == [1000, 1030]


def test_retry_after_http_date(monkeypatch, setup):
    provider, clock, sleeper = setup
    wall = 1_700_000_000.0
    provider.wall_clock = lambda: wall
    date = format_datetime(datetime.fromtimestamp(wall + 60, timezone.utc), usegmt=True)
    seen = []

    def opening(req, timeout):
        seen.append(clock.now)
        if len(seen) == 1:
            raise http_error(429, {'error': {'status': 'RESOURCE_EXHAUSTED'}},
                             {'Retry-After': date})
        return io.BytesIO(json.dumps(response()).encode())

    install_transport(monkeypatch, opening)
    provider._request({'model': 'gemini-test'})
    assert sleeper.calls == [60] and seen == [1000, 1060]

"""Gemini physical-request identity, billing evidence and log isolation."""

import io
import json
from urllib.error import HTTPError

import pytest
from fastapi.testclient import TestClient

from bookcast.adapters.gemini import GeminiRequestLimiter, GeminiTTSProvider, sanitize_gemini_error
from bookcast.adapters.gemini import classify_http
from bookcast.jobs import job_status
from bookcast.models import DialogueTurn, Manifest, PodcastScript
from bookcast.pipeline import _Runner
from bookcast.provider_api import ProviderRequestContext
from bookcast.provider_api import ErrorKind, ProviderError
from bookcast.provider_chain import ProviderChain
from bookcast.provider_config import CloudTTSConfig, ProviderSpec
from bookcast.providers import MockLLMProvider
from bookcast.speech_segments import render_segments
from bookcast.storage import write_json
from bookcast.web_api import create_app
from bookcast.web_service import Submission, WebJob
from test_gemini_retry import FakeClock, FakeSleeper, install_transport, http_error
from test_gemini_tts import response as audio_response


def provider_for(tmp_path):
    clock = FakeClock()
    sleeper = FakeSleeper(clock)
    spec = ProviderSpec(name='gemini', kind='tts', type='gemini-tts', model='gemini-test',
                        api_key_env='TEST_KEY', cloud_tts=CloudTTSConfig(send_text_to_cloud=True))
    limiter = GeminiRequestLimiter(tmp_path / 'slots.sqlite3', clock=clock)
    return GeminiTTSProvider(spec, clock=clock, sleeper=sleeper, limiter=limiter), sleeper


def context_for(tmp_path, *, job='job-A', chunk='tts_segment:0001:0001', diagnostic=None):
    return ProviderRequestContext(job_id=job, output_id='output-A', logical_chunk_id=chunk,
                                  provider='gemini', model='gemini-test',
                                  telemetry_path=tmp_path / 'usage' / 'physical_requests.jsonl',
                                  on_telemetry_degraded=diagnostic)


def records(tmp_path):
    return [json.loads(line) for line in (tmp_path / 'usage' / 'physical_requests.jsonl').read_text().splitlines()]


def test_sanitizer_drops_all_upstream_prose():
    for message in ('transcript marker', 'AIzaSyAABBCCDD1234567890abcdefghijk1234',
                    'Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.',
                    'x-goog-api-key: forbidden', 'A' * 1024):
        assert sanitize_gemini_error(message) == 'UNKNOWN'


def test_accounting_timeout_then_success(monkeypatch, tmp_path):
    monkeypatch.setenv('TEST_KEY', 'offline-key')
    provider, sleeper = provider_for(tmp_path)
    calls = []

    def opening(req, timeout):
        calls.append(req)
        if len(calls) == 1:
            raise TimeoutError('untrusted upstream prose')
        return io.BytesIO(json.dumps({'usage_metadata': {'prompt_token_count': 10,
                                                        'candidates_token_count': 20}}).encode())

    install_transport(monkeypatch, opening)
    provider._request({'mock': 'payload'}, destination=tmp_path / 'random.tmp',
                      request_context=context_for(tmp_path))
    first, second = records(tmp_path)
    assert len(calls) == 2 and sleeper.calls == [25]
    assert first['job_id'] == second['job_id'] == 'job-A'
    assert first['output_id'] == second['output_id'] == 'output-A'
    assert first['logical_chunk_id'] == second['logical_chunk_id'] == 'tts_segment:0001:0001'
    assert [first['physical_attempt_index'], second['physical_attempt_index']] == [0, 1]
    assert first['http_status'] is None and first['retry_reason'] == 'timeout'
    assert first['billing_evidence'] == 'unknown' and first['usage_available'] is False
    assert second['http_status'] == 200 and second['result'] == 'success'
    assert second['billing_evidence'] == 'confirmed' and second['usage_available'] is True


def test_http_success_without_usage_stays_billing_unknown(monkeypatch, tmp_path):
    monkeypatch.setenv('TEST_KEY', 'offline-key')
    provider, _ = provider_for(tmp_path)
    install_transport(monkeypatch, lambda req, timeout: io.BytesIO(b'{"status":"completed"}'))
    provider._request({'mock': 'payload'}, request_context=context_for(tmp_path))
    assert records(tmp_path)[0]['billing_evidence'] == 'unknown'
    assert records(tmp_path)[0]['usage_available'] is False


def test_success_survives_telemetry_disk_failure(monkeypatch, tmp_path):
    monkeypatch.setenv('TEST_KEY', 'offline-key')
    provider, _ = provider_for(tmp_path)
    install_transport(monkeypatch, lambda req, timeout: io.BytesIO(b'{"status":"completed"}'))
    diagnostics = []
    blocked = tmp_path / 'file-instead-of-directory'
    blocked.write_text('x')
    context = ProviderRequestContext('job-A', 'output-A', 'tts_segment:0001:0001',
                                     'gemini', 'gemini-test', blocked / 'physical_requests.jsonl',
                                     diagnostics.append)
    assert provider._request({'mock': 'payload'}, request_context=context) == {'status': 'completed'}
    assert provider.telemetry_degraded is True
    assert diagnostics == ['PHYSICAL_REQUEST_LOG_UNAVAILABLE']


def test_pipeline_completed_chunk_reports_telemetry_degraded(monkeypatch, tmp_path):
    monkeypatch.setenv('TEST_KEY', 'offline-key')
    provider, _ = provider_for(tmp_path)
    install_transport(monkeypatch, lambda req, timeout: io.BytesIO(json.dumps(audio_response()).encode()))
    root = tmp_path / 'job'
    root.mkdir()
    (root / 'usage').write_text('block telemetry directory')
    manifest = Manifest(job_id='job-telemetry', output_id='output-telemetry', book_id='offline',
                        source_sha256='a' * 64, source_name='offline.txt', source_format='txt', config={})
    runner = _Runner(root, manifest, ProviderChain([MockLLMProvider()]), ProviderChain([provider]))
    script = PodcastScript(chapter_id='0001', title='safe', source_locator='self-authored',
                            turns=[DialogueTurn(speaker='主持人', text='第一句。'),
                                   DialogueTurn(speaker='嘉宾', text='第二句。')])
    render_segments(runner, script)
    assert runner.manifest.steps['tts_segment:0001:0001'].status == 'completed'
    assert provider.telemetry_degraded is True
    events = [json.loads(line) for line in (root / 'logs/events.jsonl').read_text().splitlines()]
    assert any(row['event'] == 'telemetry_diagnostic'
               and row['diagnostic'] == 'PHYSICAL_REQUEST_LOG_UNAVAILABLE' for row in events)


def test_job_isolation_with_unrelated_temporary_destinations(monkeypatch, tmp_path):
    monkeypatch.setenv('TEST_KEY', 'offline-key')
    provider, _ = provider_for(tmp_path)
    install_transport(monkeypatch, lambda req, timeout: io.BytesIO(b'{"status":"completed"}'))
    first = context_for(tmp_path / 'A', job='job-A', chunk='tts_segment:0001:0001')
    second = context_for(tmp_path / 'B', job='job-B', chunk='tts_segment:0002:0001')
    provider._request({'mock': 'payload'}, destination=tmp_path / 'B' / 'random-A.tmp', request_context=first)
    provider._request({'mock': 'payload'}, destination=tmp_path / 'A' / 'random-B.tmp', request_context=second)
    assert [row['job_id'] for row in records(tmp_path / 'A')] == ['job-A']
    assert [row['job_id'] for row in records(tmp_path / 'B')] == ['job-B']
    assert records(tmp_path / 'A')[0]['logical_chunk_id'] == 'tts_segment:0001:0001'
    assert records(tmp_path / 'B')[0]['logical_chunk_id'] == 'tts_segment:0002:0001'


def test_raw_error_absent_from_all_durable_surfaces_and_api(monkeypatch, tmp_path, capsys):
    marker = 'TRANSCRIPT_MARKER_986731'
    key = 'AIza' + 'S' * 35
    token = 'Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9'
    header = 'x-goog-api-key: malicious-secret'
    payload = 'A' * 1024
    evil_status = 'EVIL_STATUS_' + marker
    raw = ' | '.join((marker, key, token, header, payload, evil_status))
    monkeypatch.setenv('TEST_KEY', key)
    web_root = tmp_path / 'web'
    web_id = '1' * 32
    root = web_root / 'jobs' / web_id / 'output' / 'core-output'
    root.mkdir(parents=True)
    provider, _ = provider_for(tmp_path)

    def opening(req, timeout):
        raise http_error(403, {'error': {'status': evil_status, 'message': raw,
                                        'details': [{'reason': evil_status}]}})

    install_transport(monkeypatch, opening)
    manifest = Manifest(job_id='job-secure', output_id='core-output', book_id='offline',
                        source_sha256='a' * 64, source_name='offline.txt', source_format='txt', config={})
    runner = _Runner(root, manifest, ProviderChain([MockLLMProvider()]), ProviderChain([provider]))
    script = PodcastScript(chapter_id='0001', title='safe', source_locator='self-authored',
                            turns=[DialogueTurn(speaker='主持人', text='合成安全测试。'),
                                   DialogueTurn(speaker='嘉宾', text='确认。')])
    with pytest.raises(ProviderError) as raised:
        render_segments(runner, script)
    assert raised.value.kind == ErrorKind.PERMISSION
    assert raised.value.error_type == raised.value.validation_reason == 'PERMISSION'
    status = job_status(str(root / 'manifest.json'))

    record = WebJob(id=web_id, request=Submission(upload_id='2' * 32), title='safe', settings={})
    write_json(web_root / 'jobs' / web_id / 'submission.json', record.model_dump(mode='json'))
    response = TestClient(create_app(web_root), base_url='http://localhost').get(f'/api/jobs/{web_id}')
    assert response.status_code == 200
    captured_stderr = capsys.readouterr().err
    surfaces = [captured_stderr, json.dumps(status), response.text]
    for path in (root / 'manifest.json', root / 'logs/events.jsonl',
                 root / 'usage/physical_requests.jsonl'):
        surfaces.append(path.read_text())
    worker_log = web_root / 'jobs' / web_id / 'worker.log'
    worker_log.write_text(captured_stderr)
    surfaces.append(worker_log.read_text())
    all_surfaces = '\n'.join(surfaces)
    for secret in (marker, key, token, header, payload, evil_status):
        assert secret not in all_surfaces
    assert 'PERMISSION' in all_surfaces
    assert records(root)[0]['billing_evidence'] == 'unknown'


def test_malicious_upstream_status_and_reason_are_mapped():
    malicious = 'arbitrary_status_with_transcript_and_AIza_secret'
    failure = classify_http(400, json.dumps({'error': {'status': malicious,
        'message': malicious, 'details': [{'reason': malicious}]}}).encode())
    assert failure.error_type == failure.validation_reason == 'DECODE_ERROR'
    assert malicious not in str(failure.__dict__)

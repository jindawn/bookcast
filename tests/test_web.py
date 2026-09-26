"""Thin HTTP client, durable dispatch, and Core recovery integration."""

import json
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from bookcast.pipeline import load_manifest
from bookcast.export import export_m4b
from bookcast.provider_api import ErrorKind, ProviderError
from bookcast.providers import MockLLMProvider
from bookcast.source_api import BookIdentity, EditionCandidate, SearchResult, SourceOffer
from bookcast.storage import job_lock, sha256_file, write_json
from bookcast.web_api import create_app
from bookcast.web_service import WebService, id_path
from bookcast.web_worker import run_worker


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    app = create_app(tmp_path / 'web')
    monkeypatch.setattr(app.state.service, 'launch', lambda identifier: None)
    with TestClient(app, base_url='http://127.0.0.1') as client:
        yield client


def upload(client, text=b'Chapter 1\nA concrete idea.\nChapter 2\nA second idea.', filename='book.txt'):
    result = client.post('/api/uploads', params={'filename': filename}, content=text,
                         headers={'content-type': 'text/plain'})
    assert result.status_code == 201, result.text
    return result.json()['upload_id']


def submit(client, upload_id=None, **kwargs):
    identifier = uuid4().hex
    result = client.post('/api/jobs', headers={'Idempotency-Key': identifier},
                         json={'upload_id': upload_id or upload(client), **kwargs})
    assert result.status_code == 202, result.text
    return identifier, result.json()


def run(client, identifier):
    run_worker(client.app.state.service.root, identifier)
    return client.get(f'/api/jobs/{identifier}').json()


def snapshot(path):
    return {p: (p.stat().st_mtime_ns, sha256_file(p)) for p in path.rglob('*') if p.is_file() and p.name != '.lock'}


def test_submission_defaults_to_twenty_minutes(client):
    identifier, pending = submit(client)
    assert pending['minutes'] == 20
    record = client.app.state.service.read(identifier)
    assert record.request.minutes == 20


def test_remove_from_library_keeps_local_job_and_audio(client):
    identifier, _ = submit(client)
    job = run(client, identifier)
    output = Path(job['directory']) / 'podcast.mp3'
    before = sha256_file(output)
    response = client.delete(f'/api/jobs/{identifier}')
    assert response.status_code == 200 and response.json() == {'removed': True}
    assert client.get('/api/jobs').json()['jobs'] == []
    assert client.app.state.service.read(identifier).removed_at
    assert sha256_file(output) == before
    assert client.get(f'/api/jobs/{identifier}').json()['state'] == 'SUCCEEDED'
    assert client.delete(f'/api/jobs/{identifier}').status_code == 200
    assert client.get('/api/jobs').json()['jobs'] == []


def test_active_job_cannot_be_removed_from_library(client):
    identifier, _ = submit(client)
    service = client.app.state.service
    with patch.object(service, 'status', return_value={'active': True}):
        response = client.delete(f'/api/jobs/{identifier}')
    assert response.status_code == 409
    assert service.read(identifier).removed_at is None
    assert len(client.get('/api/jobs').json()['jobs']) == 1


def test_m4b_download_appears_only_after_explicit_export(client):
    identifier, _ = submit(client)
    job = run(client, identifier)
    assert job['state'] == 'SUCCEEDED' and job['audio_url'] and job['m4b_url'] is None
    assert client.get(f'/api/jobs/{identifier}/audio.m4b').status_code == 409
    export_m4b(Path(job['directory']))
    job = client.get(f'/api/jobs/{identifier}').json()
    assert job['m4b_url']
    response = client.get(job['m4b_url'])
    assert response.status_code == 200 and response.headers['content-type'] == 'audio/mp4'
    assert response.content.startswith(b'\x00\x00')
    assert client.get(job['audio_url']).status_code == 200
    (Path(job['directory']) / 'podcast.mp3').write_bytes(b'broken')
    assert client.get(f'/api/jobs/{identifier}').json()['m4b_url'] is None
    assert client.get(job['m4b_url']).status_code == 409


@pytest.mark.parametrize('mode', ['summary', 'deep_read', 'two_host'])
def test_upload_core_history_audio_and_idempotency(client, mode):
    upload_id = upload(client)
    identifier, pending = submit(client, upload_id, mode=mode, minutes=3)
    assert pending['can_resume']  # persisted but intentionally not dispatched in this fixture
    job = run(client, identifier)
    assert job['state'] == 'SUCCEEDED', job
    assert job['audio_kind'] == 'mock' and job['audio_seconds'] > 0
    assert job['progress']['remaining'] == 0 and job['progress']['chapters_completed'] == 2
    path = Path(job['directory'])
    manifest = load_manifest(path / 'manifest.json')
    assert manifest.content_options == {'mode': mode, 'minutes': 3}
    before = snapshot(path)
    again = client.post('/api/jobs', headers={'Idempotency-Key': identifier},
                        json={'upload_id': upload_id, 'mode': mode, 'minutes': 3})
    assert again.status_code == 202 and len(client.get('/api/jobs').json()['jobs']) == 1
    assert client.post(f'/api/jobs/{identifier}/resume').json()['state'] == 'SUCCEEDED'
    assert snapshot(path) == before
    response = client.get(job['audio_url'], headers={'Range': 'bytes=0-31'})
    assert response.status_code == 206 and len(response.content) == 32
    assert response.headers['content-type'] == 'audio/mpeg'
    conflict = client.post('/api/jobs', headers={'Idempotency-Key': identifier}, json={'upload_id': upload_id, 'minutes': 9})
    assert conflict.status_code == 409
    # Integrity is checked in Core before serving an audio file.
    (path / 'podcast.mp3').write_bytes(b'broken')
    damaged = client.get(f'/api/jobs/{identifier}').json()
    assert damaged['can_resume'] and damaged['audio_url'] is None
    assert client.get(job['audio_url']).status_code == 409
    assert client.post(f'/api/jobs/{identifier}/resume').status_code == 202
    assert run(client, identifier)['state'] == 'SUCCEEDED'


@pytest.mark.parametrize('kind', [ErrorKind.QUOTA, ErrorKind.TIMEOUT, ErrorKind.SCHEMA])
def test_recovery_delegates_to_core_without_repeating_completed_chapters(client, kind):
    identifier, _ = submit(client)
    original = MockLLMProvider.generate_structured
    def fail(self, prompt, response_model):
        data = json.loads(prompt)
        if data['operation'] == 'analysis' and data['chapter']['id'] == '0002':
            raise ProviderError(kind)
        return original(self, prompt, response_model)
    with patch.object(MockLLMProvider, 'generate_structured', fail):
        job = run(client, identifier)
    assert job['active_error'] == kind.value
    assert job['error'] == kind.value
    assert job['progress']['error'] == kind.value
    assert job['progress']['active_error'] == kind.value
    path = Path(job['directory'])
    before = snapshot(path / 'analysis')
    assert before
    if kind == ErrorKind.SCHEMA:
        assert job['can_retry'] and not job['can_resume']
        assert client.post(f'/api/jobs/{identifier}/resume').status_code == 409
        assert client.post(f'/api/jobs/{identifier}/retry').status_code == 202
    else:
        assert job['can_resume']
        assert client.post(f'/api/jobs/{identifier}/resume').status_code == 202
    recovered = run(client, identifier)
    assert recovered['state'] == 'SUCCEEDED'
    assert recovered['error'] is None
    assert recovered['active_error'] is None
    assert recovered['progress']['error'] is None
    assert recovered['progress']['active_error'] is None
    for file, value in before.items():
        assert (file.stat().st_mtime_ns, sha256_file(file)) == value
    calls = load_manifest(path / 'manifest.json').ai_calls
    assert len([call for call in calls if call.task == 'analysis:0001:0001']) == 1
    assert any(call.error == kind.value for call in calls)


def test_http_local_boundary_input_limits_and_no_paths(client, monkeypatch):
    assert client.get('/api/jobs', headers={'host': 'evil.example'}).status_code == 400
    assert client.post('/api/uploads?filename=b.txt', headers={'origin': 'https://evil.example', 'content-type': 'text/plain'}, content=b'book').status_code == 403
    assert client.get('/api/jobs', headers={'sec-fetch-site': 'cross-site'}).status_code == 403
    for filename, data, mime, status in [
        ('x.exe', b'executable', 'application/octet-stream', 415),
        ('x.txt', b'<html>fake</html>', 'text/plain', 409),
        ('x.pdf', b'not PDF', 'application/pdf', 409),
        ('x.epub', b'not EPUB', 'application/epub+zip', 409),
        ('x.txt', b'book', 'text/html', 415),
        ('x.txt', b'', 'text/plain', 409),
    ]:
        assert client.post('/api/uploads', params={'filename': filename}, content=data, headers={'content-type': mime}).status_code == status
    identifier = upload(client, filename='../../bad\\safe.txt')
    root = id_path(client.app.state.service.root, 'uploads', identifier)
    assert (root / 'safe.txt').is_file()
    monkeypatch.setattr('bookcast.web_api.MAX_UPLOAD', 8)
    assert client.post('/api/uploads?filename=large.txt', content=b'123456789', headers={'content-type': 'text/plain'}).status_code == 413
    assert not list(client.app.state.service.root.rglob('large.txt'))
    assert not list(client.app.state.service.root.rglob('*.tmp'))
    assert client.get('/api/jobs/not-an-id').status_code == 409
    assert client.get('/api/jobs/' + 'a' * 32).status_code == 404
    assert client.post('/api/jobs', json={'source_path': '/etc/passwd'}).status_code == 422
    assert client.post('/api/jobs', headers={'Idempotency-Key': uuid4().hex}, json={'upload_id': identifier, 'minutes': 121}).status_code == 422


def test_explicit_edition_selection_and_core_source_eligibility(client, tmp_path, monkeypatch):
    candidates = [EditionCandidate(id=f'gutenberg:{n}', provider='gutenberg', identity=BookIdentity(title=f'Book {n}')) for n in (101, 102)]
    book = tmp_path / 'public.txt'; book.write_text('Chapter 1\nOriginal synthetic test content.')
    class Source:
        eligible = False
        def search(self, *args, **kwargs): return SearchResult(candidates=candidates)
        def sources(self, candidate):
            return [SourceOffer(candidate_id=candidate.id, provider='gutenberg', format='txt', local_path=str(book),
                                eligible=self.eligible, rights_category='public_domain', rights_statement='Synthetic test only')]
    source = Source()
    monkeypatch.setattr(WebService, 'source_provider', lambda self: source)
    found = client.get('/api/books/search?title=Book').json()
    assert len(found['candidates']) == 2
    body = {'search_id': found['search_id']}
    assert client.post('/api/jobs', headers={'Idempotency-Key': uuid4().hex}, json=body).status_code == 422
    assert client.post('/api/jobs', headers={'Idempotency-Key': uuid4().hex}, json={**body, 'edition': 'gutenberg:999'}).status_code == 409
    identifier = uuid4().hex
    assert client.post('/api/jobs', headers={'Idempotency-Key': identifier}, json={**body, 'edition': 'gutenberg:102'}).status_code == 202
    assert run(client, identifier)['state'] == 'FAILED_PERMANENT'
    assert not list(client.app.state.service.root.rglob('manifest.json'))
    source.eligible = True
    assert client.post(f'/api/jobs/{identifier}/retry').status_code == 202
    job = run(client, identifier)
    assert job['state'] == 'SUCCEEDED'
    metadata = json.loads((Path(job['directory']) / 'metadata.json').read_text())
    assert metadata['acquisition']['candidate_id'] == 'gutenberg:102'


def test_restart_stale_lock_history_and_provider_redaction(client, monkeypatch):
    identifier, _ = submit(client)
    root = client.app.state.service.root
    with job_lock(id_path(root, 'jobs', identifier)):
        assert client.get(f'/api/jobs/{identifier}').json()['active']
        assert client.post(f'/api/jobs/{identifier}/resume').status_code == 409
    restarted = WebService(root)
    assert restarted.status(identifier)['can_resume']
    broken = id_path(root, 'jobs', uuid4().hex); broken.mkdir(); (broken / 'submission.json').write_text('broken')
    assert len(restarted.history()['jobs']) == 1 and len(restarted.history()['errors']) == 1
    monkeypatch.setenv('OPENAI_API_KEY', 'SECRET-NOT-RETURNED')
    data = client.get('/api/providers')
    assert len(data.json()['providers']) == 2
    assert 'SECRET-NOT-RETURNED' not in data.text and 'api_key_env' not in data.text and 'base_url' not in data.text


def test_worker_capacity_preserves_pending_submission(client):
    identifier, _ = submit(client)
    service = client.app.state.service
    class ActiveChild:
        def poll(self): return None
    service.children = {'first': ActiveChild(), 'second': ActiveChild()}
    from bookcast.errors import BookCastError
    with pytest.raises(BookCastError, match='两个后台任务'):
        WebService.launch(service, identifier)
    assert service.read(identifier).id == identifier
    assert service.status(identifier)['can_resume']


def test_real_detached_worker_survives_application_object_restart(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    app = create_app(tmp_path / 'web')
    with TestClient(app, base_url='http://127.0.0.1') as client:
        identifier, _ = submit(client)
        process = app.state.service.children[identifier]
        try:
            restarted = WebService(tmp_path / 'web')
            deadline = time.monotonic() + 30
            while process.poll() is None and time.monotonic() < deadline:
                time.sleep(.05)
            assert process.poll() == 0
            assert restarted.status(identifier)['state'] == 'SUCCEEDED'
        finally:
            if process.poll() is None:
                process.kill(); process.wait(timeout=5)


def test_web_reads_actual_speech_kind_from_core(client, tmp_path, monkeypatch):
    from bookcast.tts_setup import write_local_config
    from test_tts import UnitFake
    config = tmp_path / 'tts.toml'
    write_local_config(config, tmp_path / 'models')
    client.app.state.service.config = config
    monkeypatch.setattr('bookcast.adapters.kokoro.KokoroTTSProvider', lambda spec: UnitFake(spec.name))
    identifier, _ = submit(client, minutes=1)
    job = run(client, identifier)
    assert job['state'] == 'SUCCEEDED' and job['audio_kind'] == 'speech'
    assert job['audio_seconds'] > 0
    assert client.get(job['audio_url']).status_code == 200
    # Historical audio type remains true even if the currently selected config changes.
    client.app.state.service.config = None
    assert client.get(f'/api/jobs/{identifier}').json()['audio_kind'] == 'speech'


def test_web_retry_uses_saved_real_tts_and_keeps_completed_analysis(client, tmp_path, monkeypatch):
    from bookcast.tts_setup import write_local_config
    from test_tts import UnitFake
    config = tmp_path / 'tts.toml'
    write_local_config(config, tmp_path / 'models')
    client.app.state.service.config = config
    native = UnitFake('kokoro')
    monkeypatch.setattr('bookcast.adapters.kokoro.KokoroTTSProvider', lambda spec: native)
    identifier, _ = submit(client, minutes=1)
    original = MockLLMProvider.generate_structured
    def fail_second(self, prompt, response_model):
        data = json.loads(prompt)
        if data['operation'] == 'analysis' and data['chapter']['id'] == '0002':
            raise ProviderError(ErrorKind.SCHEMA)
        return original(self, prompt, response_model)
    with patch.object(MockLLMProvider, 'generate_structured', fail_second):
        failed = run(client, identifier)
    assert failed['state'] == 'FAILED_PERMANENT' and not native.calls
    path = Path(failed['directory'])
    before = snapshot(path / 'analysis')
    saved = client.app.state.service.read(identifier)
    assert saved.settings['config']['tts_priority'] == ['kokoro']
    assert failed['task_providers']['tts'] == ['kokoro']
    assert client.post(f'/api/jobs/{identifier}/retry').status_code == 202
    done = run(client, identifier)
    assert done['state'] == 'SUCCEEDED' and done['audio_kind'] == 'speech' and native.calls
    assert done['task_providers']['tts'] == ['kokoro']
    assert {c.provider for c in load_manifest(path / 'manifest.json').ai_calls if c.kind == 'tts'} == {'kokoro'}
    assert all((p.stat().st_mtime_ns, sha256_file(p)) == value for p, value in before.items())


def test_web_hides_completed_mock_audio_when_saved_selection_is_real(client, tmp_path, monkeypatch):
    from bookcast.tts_setup import write_local_config
    from bookcast.provider_config import load_config
    from test_tts import UnitFake
    identifier, _ = submit(client, minutes=1)
    mock = run(client, identifier)
    assert mock['state'] == 'SUCCEEDED' and mock['audio_kind'] == 'mock'
    path = Path(mock['directory'])
    config = tmp_path / 'real-tts.toml'
    write_local_config(config, tmp_path / 'models')
    real_settings = {'config': load_config(config).model_dump(mode='json'),
                     'llm_selection': 'auto', 'tts_selection': 'auto'}
    manifest = load_manifest(path / 'manifest.json')
    manifest.provider_settings = real_settings
    write_json(path / 'manifest.json', manifest.model_dump(mode='json'))
    status = client.get(f'/api/jobs/{identifier}').json()
    assert status['state'] == 'FAILED_RETRYABLE' and status['audio_url'] is None
    native = UnitFake('kokoro')
    monkeypatch.setattr('bookcast.adapters.kokoro.KokoroTTSProvider', lambda spec: native)
    assert client.post(f'/api/jobs/{identifier}/resume').status_code == 202
    done = run(client, identifier)
    assert done['state'] == 'SUCCEEDED' and done['audio_kind'] == 'speech'
    assert native.calls and done['audio_url']


def test_frontend_has_no_core_implementation_or_vendor_sdk():
    root = Path(__file__).parents[1]
    page = (root / 'web/app/page.tsx').read_text()
    assert '/api/jobs' in page and '/api/uploads' in page and '/api/providers' in page
    assert all(token not in page.lower() for token in ('openai', 'ffmpeg', 'pymupdf', 'ebooklib', 'api_key', 'child_process'))
    deps = json.loads((root / 'web/package.json').read_text())['dependencies']
    assert set(deps) == {'next', 'react', 'react-dom'}


def test_sigkill_web_worker_then_restart_resume_preserves_completed_analysis(client, tmp_path):
    identifier, _ = submit(client)
    root = client.app.state.service.root
    ready = tmp_path / 'worker-ready'
    script = '''
import json, sys, time
from pathlib import Path
from bookcast.providers import MockLLMProvider
from bookcast.web_worker import run_worker
original = MockLLMProvider.generate_structured
def stop(self, prompt, response_model):
    data = json.loads(prompt)
    if data['operation']=='analysis' and data['chapter']['id']=='0002':
        Path(sys.argv[3]).write_text('ready')
        while True: time.sleep(.05)
    return original(self,prompt,response_model)
MockLLMProvider.generate_structured = stop
run_worker(Path(sys.argv[1]),sys.argv[2])
'''
    process = subprocess.Popen([sys.executable, '-c', script, str(root), identifier, str(ready)],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        deadline = time.monotonic() + 20
        while not ready.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(.02)
        assert ready.exists()
        assert client.get(f'/api/jobs/{identifier}').json()['active']
        assert client.post(f'/api/jobs/{identifier}/resume').status_code == 409
        path = client.app.state.service.core_path(identifier).parent
        before = snapshot(path / 'analysis')
        process.kill(); process.wait(timeout=5)
        restarted = WebService(root)
        assert restarted.status(identifier)['can_resume']
        restarted.dispatch(identifier)
        child = restarted.children[identifier]
        try:
            child.wait(timeout=20)
            assert child.returncode == 0 and restarted.status(identifier)['state'] == 'SUCCEEDED'
        finally:
            if child.poll() is None:
                child.kill(); child.wait(timeout=5)
        for file, value in before.items():
            assert (file.stat().st_mtime_ns, sha256_file(file)) == value
        assert len([c for c in load_manifest(path / 'manifest.json').ai_calls if c.task == 'analysis:0001:0001']) == 1
    finally:
        if process.poll() is None:
            process.kill(); process.wait(timeout=5)

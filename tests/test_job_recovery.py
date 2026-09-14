"""Real process death, quota/config changes and authoritative task states."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest
from bookcast.errors import BookCastError
from bookcast.models import Artifact, Attempt, Job, Step, TaskState
from bookcast.pipeline import Pipeline, load_manifest
from bookcast.jobs import job_status
from bookcast.provider_api import ErrorKind, ProviderError
from bookcast.provider_chain import ProviderChain
from bookcast.providers import MockLLMProvider, MockTTSProvider
from bookcast.storage import sha256_file, fingerprint, write_json


class Recording(MockLLMProvider):
    def __init__(self, name='A', fail=None, config='v1'):
        self.name, self.fail, self.cache_key, self.calls = name, fail, config, []
    def generate_structured(self, prompt, response_model):
        data=json.loads(prompt)
        self.calls.append(data)
        if self.fail:
            self.fail(data)
        return super().generate_structured(prompt,response_model)


def snapshot(root):
    return {p:(p.stat().st_mtime_ns,sha256_file(p)) for p in root.rglob('*') if p.is_file() and p.name!='.lock'}


def unchanged(before):
    assert all((p.stat().st_mtime_ns,sha256_file(p))==v for p,v in before.items())


KILL_SCRIPT = r'''
import json, sys, time
from pathlib import Path
import bookcast.pipeline as implementation
from bookcast.models import BookMetadata
from bookcast.providers import MockLLMProvider, MockTTSProvider
from bookcast.storage import sha256_file
source, output, ready, at = map(Path,sys.argv[1:5])
def stop():
    ready.write_text('ready')
    while True: time.sleep(.05)
class Blocking(MockLLMProvider):
    name='A'
    cache_key='v1'
    def generate_structured(self,prompt,response_model):
        data=json.loads(prompt)
        if str(at)=='invoke' and data['operation']=='analysis' and data['chapter']['id']=='0007': stop()
        return super().generate_structured(prompt,response_model)
observe=implementation._Runner.observe
def observed(self,attempt):
    observe(self,attempt)
    if str(at)=='completed' and attempt.task=='analysis:0007:0001' and attempt.status=='completed': stop()
implementation._Runner.observe=observed
parser=implementation.parse_book
def parse(*args):
    if str(at)=='parse': stop()
    return parser(*args)
implementation.parse_book=parse
seed=BookMetadata(book_id='seed',title='Saved acquisition title',source_name=source.name,
    source_sha256=sha256_file(source),source_format='txt',acquisition={'test_provenance':'retained'})
implementation.Pipeline(Blocking(),MockTTSProvider(),output).generate(source,metadata_seed=seed)
'''


@pytest.mark.parametrize('at',['invoke','completed','parse'])
def test_sigkill_active_exclusion_stale_and_source_independent_resume(tmp_path,at):
    source=tmp_path/'source.txt'
    source.write_text('\n'.join(f'Chapter {n}\nTopic {n}.' for n in range(1,9)))
    output,ready=tmp_path/'out',tmp_path/'ready'
    process=subprocess.Popen([sys.executable,'-c',KILL_SCRIPT,str(source),str(output),str(ready),at],
                              stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,text=True)
    try:
        deadline=time.monotonic()+30
        while not ready.exists() and process.poll() is None and time.monotonic()<deadline:
            time.sleep(.02)
        assert ready.exists(), process.communicate(timeout=2)[1] if process.poll() is not None else 'no sentinel'
        job=next(output.iterdir())
        raw=(job/'manifest.json').read_bytes()
        active=job_status(str(job))
        assert active['active'] and not active['stale']
        assert active['progress']['remaining']>0 and not active['progress']['total_final']
        with pytest.raises(BookCastError,match='另一个进程'):
            Pipeline(Recording('B'),MockTTSProvider(),output).resume_job(job)
        assert (job/'manifest.json').read_bytes()==raw
        process.kill()
        process.wait(timeout=10)
        stale=job_status(str(job))
        assert stale['stale'] and not stale['active'] and stale['effective_state']=='FAILED_RETRYABLE'
        assert (job/'manifest.json').read_bytes()==raw  # read-only status never repairs behind a live worker
        before=snapshot(job/'analysis') if (job/'analysis').exists() else {}
        source.rename(tmp_path/'moved-original.txt')
        b=Recording('B')
        Pipeline(b,MockTTSProvider(),output).resume_job(job)
        unchanged(before)
        m=load_manifest(job/'manifest.json')
        assert isinstance(m,Job) and m.state==TaskState.SUCCEEDED and m.owner is None
        assert m.schema_version==3 and len(m.job_id)==32
        metadata=json.loads((job/'metadata.json').read_text())
        assert metadata['title']=='Saved acquisition title' and metadata['acquisition']=={'test_provenance':'retained'}
        seen=[d['chapter']['id'] for d in b.calls if d['operation']=='analysis']
        assert seen==(['0007','0008'] if at=='invoke' else ['0008'] if at=='completed' else [f'{n:04}' for n in range(1,9)])
        assert not job_status(str(job))['stale']
        events=[json.loads(line) for line in (job/'logs/events.jsonl').read_text().splitlines()]
        assert any(e['event']=='stale_recovered' for e in events)
        assert any(e['state']=='SKIPPED' for e in events)
    finally:
        if process.poll() is None:
            process.kill(); process.wait(timeout=10)
        if process.stderr: process.stderr.close()


def test_quota_exhaustion_and_explicit_replacement_retain_completed_chapters(tmp_path):
    source=tmp_path/'book.txt';source.write_text('\n'.join(f'Chapter {n}\nPoint {n}.' for n in range(1,9)))
    def fail(d):
        if d['operation']=='analysis' and d['chapter']['id']=='0007': raise ProviderError(ErrorKind.QUOTA)
    with pytest.raises(BookCastError): Pipeline(Recording(fail=fail),MockTTSProvider(),tmp_path/'out').generate(source)
    job=next((tmp_path/'out').iterdir());m=load_manifest(job/'manifest.json')
    assert m.state==TaskState.FAILED_RETRYABLE and m.steps['analysis:0007:0001'].state==TaskState.FAILED_RETRYABLE
    assert m.steps['analysis:0008:0001'].state==TaskState.PENDING
    before=snapshot(job/'analysis');b=Recording('B')
    Pipeline(b,MockTTSProvider(),tmp_path/'out').resume_job(job)
    unchanged(before)
    assert [d['chapter']['id'] for d in b.calls if d['operation']=='analysis']==['0007','0008']
    m=load_manifest(job/'manifest.json')
    assert all(isinstance(a,Attempt) and a.provider_config_hash for a in m.ai_calls)
    assert all(isinstance(a,Artifact) and a.size_bytes>0 for a in m.artifact_records.values())
    assert m.artifact_records['analysis/chunks/0001-0001.json'].provider=='A'
    assert m.artifact_records['analysis/chunks/0007-0001.json'].provider=='B'


def test_permanent_error_requires_retry_and_history_survives(tmp_path):
    source=tmp_path/'b.txt';source.write_text('Chapter 1\nA point.')
    def fail(d):
        if d['operation']=='dialogue': raise ProviderError(ErrorKind.SCHEMA)
    with pytest.raises(BookCastError): Pipeline(Recording(fail=fail),MockTTSProvider(),tmp_path/'out').generate(source)
    job=next((tmp_path/'out').iterdir());before=snapshot(job);b=Recording('B')
    with pytest.raises(BookCastError,match='retry'): Pipeline(b,MockTTSProvider(),tmp_path/'out').resume_job(job)
    unchanged(before);assert not b.calls
    Pipeline(b,MockTTSProvider(),tmp_path/'out').resume_job(job,retry=True)
    assert [d['operation'] for d in b.calls]==['dialogue','consistency']
    assert any(c.state==TaskState.FAILED_PERMANENT for c in load_manifest(job/'manifest.json').ai_calls)


def test_cache_restart_same_name_config_and_prompt_version(tmp_path,monkeypatch):
    source=tmp_path/'b.txt';source.write_text('Chapter 1\nA point.')
    a=Recording();pipe=Pipeline(a,MockTTSProvider(),tmp_path/'out');job=pipe.generate(source)
    before=snapshot(job);same=Recording();Pipeline(same,MockTTSProvider(),tmp_path/'out').resume_job(job)
    unchanged(before);assert not same.calls
    newer=Recording(config='v2')
    Pipeline(newer,MockTTSProvider(),tmp_path/'out').resume_job(job)
    assert [d['operation'] for d in newer.calls]==['analysis','synthesis','synthesis','dialogue','consistency']
    old=load_manifest(job/'manifest.json').artifact_records['analysis/chunks/0001-0001.json']
    # Change a real prompt input while keeping the provider instance unchanged.
    import bookcast.content as content
    monkeypatch.setitem(content.INSTRUCTIONS,'analysis',content.INSTRUCTIONS['analysis']+' 提示修订。')
    fresh=Recording(config='v2');Pipeline(fresh,MockTTSProvider(),tmp_path/'out').resume_job(job)
    assert [d['operation'] for d in fresh.calls]==['analysis']
    now=load_manifest(job/'manifest.json').artifact_records['analysis/chunks/0001-0001.json']
    assert old.cache_key!=now.cache_key and old.provider_config_hash==now.provider_config_hash


def test_damage_only_regenerates_necessary_task_and_output_window(tmp_path):
    source=tmp_path/'b.txt';source.write_text('Chapter 1\nA point.')
    job=Pipeline(Recording(),MockTTSProvider(),tmp_path/'out').generate(source)
    (job/'scripts/0001.json').write_text('damaged')
    b=Recording();Pipeline(b,MockTTSProvider(),tmp_path/'out').resume_job(job)
    assert [d['operation'] for d in b.calls]==['dialogue']
    assert job_status(str(job))['integrity']=='ok'


def test_states_roundtrip_and_initial_atomic_temp_is_recoverable(tmp_path):
    for status,state in [('pending','PENDING'),('running','RUNNING'),('completed','SUCCEEDED'),
                         ('failed_retryable','FAILED_RETRYABLE'),('failed_permanent','FAILED_PERMANENT'),('skipped','SKIPPED')]:
        step=Step(status=status)
        assert step.state.value==state and Step.model_validate_json(step.model_dump_json()).state==step.state
    source=tmp_path/'b.txt';source.write_text('Chapter 1\nA point.')
    bid=fingerprint({'source_sha256':sha256_file(source),'format':'txt'})[:24]
    root=tmp_path/'out'/bid;root.mkdir(parents=True)
    owned=root/'.manifest.json.crashed.tmp';owned.write_text('{')
    Pipeline(Recording(),MockTTSProvider(),tmp_path/'out').generate(source)
    assert owned.read_text()=='{' and (root/'podcast.mp3').is_file()


def test_prompt_version_invalidates_and_is_recorded(tmp_path,monkeypatch):
    import bookcast.content as content
    source=tmp_path/'b.txt';source.write_text('Chapter 1\nA point.')
    job=Pipeline(Recording(),MockTTSProvider(),tmp_path/'out').generate(source)
    monkeypatch.setattr(content,'CONTENT_VERSION','content-test-v2')
    class Updated(Recording):
        def generate_structured(self,prompt,response_model):
            # Offline fixture understands the same operations under a new prompt version.
            data=json.loads(prompt);self.calls.append(data)
            data['prompt_version']='content-v1'
            return MockLLMProvider.generate_structured(self,json.dumps(data),response_model)
    updated=Updated();Pipeline(updated,MockTTSProvider(),tmp_path/'out').resume_job(job)
    assert len(updated.calls)==5
    m=load_manifest(job/'manifest.json')
    assert m.artifact_records['analysis/chunks/0001-0001.json'].prompt_version=='content-test-v2'
    assert all(c.prompt_version=='content-test-v2' for c in m.ai_calls[-5:])


def test_stale_after_permanent_step_failure_does_not_implicitly_retry(tmp_path):
    source=tmp_path/'b.txt';source.write_text('Chapter 1\nA point.')
    job=Pipeline(Recording(),MockTTSProvider(),tmp_path/'out').generate(source)
    m=load_manifest(job/'manifest.json');m.status='running';m.steps['script:0001'].status='failed_permanent'
    m.steps['script:0001'].error_kind='schema_error';write_json(job/'manifest.json',m.model_dump())
    report=job_status(str(job));assert report['stale'] and report['effective_state']=='FAILED_PERMANENT'
    b=Recording()
    with pytest.raises(BookCastError,match='retry'): Pipeline(b,MockTTSProvider(),tmp_path/'out').resume_job(job)
    assert not b.calls


def test_changed_plan_retires_obsolete_failed_tasks_as_skipped(tmp_path):
    source=tmp_path/'b.txt';source.write_text('Chapter 1\nFirst topic.\nChapter 2\nSecond topic.')
    def fail(d):
        if d['operation']=='dialogue' and d['segment']['id']=='0002': raise ProviderError(ErrorKind.SCHEMA)
    with pytest.raises(BookCastError): Pipeline(Recording(fail=fail),MockTTSProvider(),tmp_path/'out').generate(source)
    job=next((tmp_path/'out').iterdir())
    class Deduplicating(Recording):
        def generate_structured(self,prompt,response_model):
            result=super().generate_structured(prompt,response_model)
            if json.loads(prompt)['operation']=='synthesis':
                for theme in result.themes: theme.summary='A shared topic'
            return result
    # Same provider name but changed configuration intentionally invalidates its content cache.
    provider=Deduplicating(config='changed')
    Pipeline(provider,MockTTSProvider(),tmp_path/'out').resume_job(job,retry=True)
    m=load_manifest(job/'manifest.json')
    assert m.steps['script:0002'].state==TaskState.SKIPPED
    assert m.steps['script:0002'].skip_reason=='not_in_current_plan'
    assert job_status(str(job))['effective_state']=='SUCCEEDED'
    provider.calls.clear()
    Pipeline(provider,MockTTSProvider(),tmp_path/'out').resume_job(job)
    assert not provider.calls

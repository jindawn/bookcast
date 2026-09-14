import json
from pathlib import Path
from unittest.mock import patch
import pytest
from typer.testing import CliRunner

from bookcast.cli import app
from bookcast.jobs import job_status, list_jobs, resolve_job
from bookcast.pipeline import Pipeline, load_manifest
from bookcast.models import RunOwner
from bookcast.providers import MockLLMProvider, MockTTSProvider
from bookcast.provider_api import ProviderError, ErrorKind
from bookcast.provider_config import load_config
from bookcast.provider_registry import default_registry
from bookcast.storage import sha256_file, write_json


def files(root):
    return {str(p):(p.stat().st_mtime_ns,sha256_file(p)) for p in root.rglob('*') if p.is_file() and p.name!='.lock'}


def generated(tmp_path,monkeypatch):
    monkeypatch.chdir(tmp_path)
    source=tmp_path/'example.txt';source.write_text('Chapter 1\nA point.')
    runner=CliRunner()
    result=runner.invoke(app,['generate',str(source)])
    assert result.exit_code==0,result.output
    job=next((tmp_path/'output').iterdir())
    return runner,source,job


def test_cli_progress_discovery_saved_configuration_and_source_independence(tmp_path,monkeypatch):
    runner,source,job=generated(tmp_path,monkeypatch)
    m=load_manifest(job/'manifest.json');before=files(job)
    assert m.provider_settings['config']['llm_priority']==['mock']
    result=runner.invoke(app,['jobs','--json']);listing=json.loads(result.stdout)
    assert listing['jobs'][0]['job_id']==m.job_id and listing['jobs'][0]['state']=='SUCCEEDED'
    result=runner.invoke(app,['status',m.job_id,'--json']);report=json.loads(result.stdout)
    assert report['progress']['remaining']==0 and report['progress']['total_final']
    assert report['progress']['chapters_completed']==1
    assert files(job)==before  # read-only discovery
    # A malicious/unrelated CWD config must not be read on resume.
    (tmp_path/'bookcast.toml').write_text('api_key="DO-NOT-LOG"')
    result=runner.invoke(app,['generate',str(source),'--resume'])
    assert result.exit_code==0,result.output
    source.rename(tmp_path/'moved.txt')
    for command in ('resume','retry'):
        result=runner.invoke(app,[command,m.job_id])
        assert result.exit_code==0,result.output
        assert 'DO-NOT-LOG' not in result.output
    assert files(job)==before
    result=runner.invoke(app,['status',m.job_id])
    assert result.exit_code==0 and 'SUCCEEDED' in result.stdout
    assert all(label in result.stderr for label in ('章节','Provider','完成','剩余','最近错误'))


def test_jobs_ignore_symlinks_isolate_corruption_and_reject_ambiguous_book_id(tmp_path,monkeypatch):
    runner,source,job=generated(tmp_path,monkeypatch)
    m=load_manifest(job/'manifest.json')
    other=Pipeline(MockLLMProvider(),MockTTSProvider(),tmp_path/'output'/'other').generate(source,mode='summary')
    broken=tmp_path/'output'/'broken';broken.mkdir();(broken/'manifest.json').write_text('broken')
    outside=tmp_path/'outside';outside.mkdir();(outside/'manifest.json').write_text('{}')
    (tmp_path/'output'/'symlink').symlink_to(outside,target_is_directory=True)
    listing=list_jobs(tmp_path/'output')
    assert len(listing['jobs'])==2 and len(listing['errors'])==1
    assert resolve_job(m.job_id,tmp_path/'output')==job/'manifest.json'
    with pytest.raises(Exception,match='多个目录'): resolve_job(m.book_id,tmp_path/'output')
    assert resolve_job(str(other),tmp_path/'output')==other/'manifest.json'
    assert runner.invoke(app,['resume','../escape']).exit_code==1
    assert runner.invoke(app,['jobs']).exit_code==0


def test_doctor_reports_stale_and_corrupt_without_modifying_them(tmp_path,monkeypatch):
    runner,source,job=generated(tmp_path,monkeypatch)
    m=load_manifest(job/'manifest.json');m.status='running';m.owner=RunOwner(session_id='old-boot',pid=1,hostname='other')
    write_json(job/'manifest.json',m.model_dump())
    broken=tmp_path/'output'/'bad';broken.mkdir();(broken/'manifest.json').write_text('bad')
    before=files(tmp_path/'output')
    result=runner.invoke(app,['doctor']);data=json.loads(result.stdout)
    assert result.exit_code==0 and data['jobs']['stale']==[m.job_id] and len(data['jobs']['corrupt'])==1
    assert files(tmp_path/'output')==before
    assert runner.invoke(app,['resume',m.job_id]).exit_code==0
    assert not job_status(str(job))['stale']


def test_cli_quota_switch_and_permanent_retry(tmp_path,monkeypatch):
    monkeypatch.chdir(tmp_path)
    config=tmp_path/'providers.toml';config.write_text('''schema_version = 1
llm_priority = ["A", "B"]
tts_priority = ["tone"]
[[providers]]
name="A"
kind="llm"
type="mock"
model="mock-llm-v1"
[[providers]]
name="B"
kind="llm"
type="mock"
model="mock-llm-v1"
[[providers]]
name="tone"
kind="tts"
type="mock"
model="mock-tones-v1"
''')
    source=tmp_path/'book.txt';source.write_text('Chapter 1\nTopic.\nChapter 2\nAnother topic.')
    original=MockLLMProvider.generate_structured
    def fail(self,prompt,response_model):
        data=json.loads(prompt)
        if self.name=='A' and data['operation']=='analysis' and data['chapter']['id']=='0002':
            raise ProviderError(ErrorKind.QUOTA)
        return original(self,prompt,response_model)
    runner=CliRunner()
    with patch.object(MockLLMProvider,'generate_structured',fail):
        result=runner.invoke(app,['generate',str(source),'--config',str(config),'--provider','A'])
        assert result.exit_code==1
        job=next((tmp_path/'output').iterdir());m=load_manifest(job/'manifest.json')
        assert 'quota_exhausted' in result.stderr
        result=runner.invoke(app,['resume',m.job_id,'--provider','B'])
        assert result.exit_code==0,result.output
    calls=load_manifest(job/'manifest.json').ai_calls
    assert [(c.provider,c.task) for c in calls if c.task=='analysis:0001:0001']==[('A','analysis:0001:0001')]
    assert load_manifest(job/'manifest.json').provider_settings['llm_selection']=='B'
    # Force an explicitly recorded permanent failure, then ensure only retry clears it.
    m=load_manifest(job/'manifest.json');m.status='failed';m.error_kind='schema_error'
    write_json(job/'manifest.json',m.model_dump());before=files(job)
    assert runner.invoke(app,['resume',m.job_id]).exit_code==1
    assert files(job)==before
    assert runner.invoke(app,['retry',m.job_id]).exit_code==0


def test_legacy_v2_migration_preserves_parse_and_requires_explicit_nondefault_config(tmp_path,monkeypatch):
    runner,source,job=generated(tmp_path,monkeypatch)
    m=load_manifest(job/'manifest.json');m.schema_version=2;m.job_id=None;m.provider_settings=None
    write_json(job/'manifest.json',m.model_dump());raw=(job/'manifest.json').read_bytes()
    before=files(job/'analysis')
    result=runner.invoke(app,['resume',m.book_id]);assert result.exit_code==0,result.output
    assert (job/'manifest.v2.json').read_bytes()==raw and load_manifest(job/'manifest.json').schema_version==3
    assert files(job/'analysis')==before
    # No matching default snapshot: do not guess an endpoint or make network calls.
    m=load_manifest(job/'manifest.json');m.provider_settings=None;m.config['llm']='unknown'
    write_json(job/'manifest.json',m.model_dump());before=files(job)
    result=runner.invoke(app,['resume',m.job_id]);assert result.exit_code==1 and '--config' in result.stderr
    assert files(job)==before


def test_broken_progress_sink_never_repeats_ai_calls(tmp_path):
    source=tmp_path/'book.txt';source.write_text('Chapter 1\nTopic.')
    def broken(event): raise BrokenPipeError('terminal closed')
    pipe=Pipeline(MockLLMProvider(),MockTTSProvider(),tmp_path/'out',progress=broken)
    job=pipe.generate(source);before=files(job)
    pipe.resume_job(job)
    assert files(job)==before

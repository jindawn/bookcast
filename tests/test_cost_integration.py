"""Offline RC cost flow from provider Attempt to CLI and Web status."""

import json
from pathlib import Path
from uuid import uuid4

import pytest
from typer.testing import CliRunner

from bookcast.cli import app
from bookcast.cost import calculate_cost_summary, read_valid_cost_summary, refresh_cost_summary
from bookcast.errors import BookCastError
from bookcast.generation import ProviderUsage
from bookcast.jobs import job_status
from bookcast.models import Attempt
from bookcast.pipeline import Pipeline, load_manifest
from bookcast.provider_config import ProvidersConfig
from bookcast.providers import MockLLMProvider, MockTTSProvider
from bookcast.storage import write_json
from bookcast.web_service import Submission, WebJob, WebService, id_path


class UsageMockLLM(MockLLMProvider):
    name = 'priced'
    model = 'deepseek-flash'
    cache_key = 'offline-priced-llm'

    def generate_structured(self, prompt, response_model):
        value = super().generate_structured(prompt, response_model)
        self.last_usage = ProviderUsage(input_tokens=1_500_000, cache_hit_tokens=500_000,
                                        output_tokens=1_000_000)
        return value


def settings(rate=2.0):
    config = ProvidersConfig.model_validate({
        'providers': [
            {'name': 'priced', 'kind': 'llm', 'type': 'mock', 'model': 'deepseek-flash'},
            {'name': 'mock', 'kind': 'tts', 'type': 'mock', 'model': 'mock-tones-v1'},
        ],
        'llm_priority': ['priced'], 'tts_priority': ['mock'],
        'pricing': {'deepseek-flash': {'default': {
            'cached_input_per_million': 1.0,
            'uncached_input_per_million': rate,
            'output_per_million': 3.0}}},
    })
    return {'config': config.model_dump(mode='json'), 'llm_selection': 'auto', 'tts_selection': 'auto'}


def create_job(tmp_path, *, output_dir=None, text='第一章\n协作有收益。\n第二章\n分工也有成本。'):
    tmp_path.mkdir(parents=True, exist_ok=True)
    source = tmp_path / f'{uuid4().hex}.txt'
    source.write_text(text, encoding='utf-8')
    root = Pipeline(UsageMockLLM(), MockTTSProvider(), output_dir or tmp_path/'output',
                    provider_settings=settings()).generate(source, minutes=1)
    return root, load_manifest(root/'manifest.json')


def test_pipeline_attempt_to_usage_cost_status_and_web_dto(tmp_path):
    web_id = uuid4().hex
    service = WebService(tmp_path/'web')
    web_root = id_path(service.root, 'jobs', web_id)
    web_root.mkdir(parents=True)
    root, manifest = create_job(tmp_path, output_dir=web_root/'output')
    write_json(web_root/'submission.json', WebJob(id=web_id, request=Submission(upload_id='f'*32),
               title='离线成本', settings=settings(), status='SUCCEEDED').model_dump(mode='json'))

    llm_usage = json.loads((root/'usage/llm_usage.json').read_text())
    tts_usage = json.loads((root/'usage/tts_usage.json').read_text())
    stored = json.loads((root/'usage/cost_summary.json').read_text())
    assert llm_usage['total']['request_count'] > 0
    assert tts_usage['tts_usage'][0]['request_count'] > 0
    assert manifest.cost_snapshot['config']['pricing']['deepseek-flash']['default']['uncached_input_per_million'] == 2.0
    assert stored['job_identity']['job_id'] == manifest.job_id
    assert stored['job_identity']['output_id'] == manifest.output_id == root.name
    assert stored['job_identity']['source_hash'] == manifest.source_sha256
    assert stored['llm']['providers']['priced::deepseek-flash']['cost']['status'] == 'actual'
    assert stored['total']['known_amount'] > 0
    assert stored['tts']['providers']['mock::mock-tones-v1']['cost']['status'] == 'unavailable'
    assert job_status(str(root))['cost_summary'] == stored
    assert service.status(web_id)['cost_summary'] == stored


def test_cli_uses_creation_snapshot_not_current_toml_or_updated_provider_settings(tmp_path, monkeypatch):
    root, manifest = create_job(tmp_path)
    original = json.loads((root/'usage/cost_summary.json').read_text())
    changed = settings(rate=99.0)
    manifest.provider_settings = changed  # A resume may change the current Provider selection.
    write_json(root/'manifest.json', manifest.model_dump(mode='json'))
    (tmp_path/'bookcast.toml').write_text('[pricing.deepseek-flash.default]\n'
        'cached_input_per_million = 99\nuncached_input_per_million = 99\noutput_per_million = 99\n')
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(app, ['cost', str(root)])
    assert result.exit_code == 0, result.output
    assert f'Job ID: {manifest.job_id}' in result.output
    assert f'Output ID: {manifest.output_id}' in result.output
    assert json.loads((root/'usage/cost_summary.json').read_text())['total']['known_amount'] == original['total']['known_amount']
    rejected = CliRunner().invoke(app, ['cost', str(root), '--config', str(tmp_path/'bookcast.toml')])
    assert rejected.exit_code == 1
    assert '创建时' in rejected.output


def test_resume_without_new_attempt_refreshes_changed_selection(tmp_path):
    root, manifest = create_job(tmp_path)
    before = json.loads((root/'usage/cost_summary.json').read_text())
    attempts = len(manifest.ai_calls)
    changed = settings(rate=99.0)
    Pipeline(UsageMockLLM(), MockTTSProvider(), tmp_path/'output',
             provider_settings=changed).generate(Path(manifest.source_path), minutes=1, resume=True)
    after = json.loads((root/'usage/cost_summary.json').read_text())
    resumed = load_manifest(root/'manifest.json')
    assert len(resumed.ai_calls) == attempts
    assert after['job_identity']['selection_hash'] != before['job_identity']['selection_hash']
    assert after['total']['known_amount'] == before['total']['known_amount']
    assert job_status(str(root))['cost_summary'] == after


def test_partial_usage_and_provider_model_rows():
    config = ProvidersConfig.model_validate(settings()['config'])
    config.pricing['future-model'] = config.pricing['deepseek-flash']
    def attempt(identifier, model, usage):
        return Attempt(id=identifier, task='analysis:0001:0001', kind='llm', status='completed',
                       provider='priced', model=model, prompt_version='v1', input_hash='x',
                       timestamp='2026-09-28T06:30:00Z', provider_reported_usage=usage)
    complete = ProviderUsage(input_tokens=1_500_000, cache_hit_tokens=500_000, output_tokens=1_000_000)
    missing_cache = ProviderUsage(input_tokens=1_500_000, output_tokens=1_000_000)
    summary = calculate_cost_summary([
        attempt('a', 'deepseek-flash', complete),
        attempt('b', 'deepseek-flash', missing_cache),
        attempt('c', 'future-model', complete),
    ], config)
    rows = summary['llm']['providers']
    assert set(rows) == {'priced::deepseek-flash', 'priced::future-model'}
    assert rows['priced::deepseek-flash']['requests'] == 2
    assert rows['priced::deepseek-flash']['cost']['status'] == 'partial'
    assert rows['priced::deepseek-flash']['cost']['amount'] == 5.5
    assert rows['priced::future-model']['cost']['status'] == 'actual'
    assert summary['total']['known_amount'] == 11.0
    assert summary['total']['status'] == 'partial'


def test_job_isolation_and_stale_identity(tmp_path):
    a, ma = create_job(tmp_path/'a', output_dir=tmp_path/'a'/'out')
    b, mb = create_job(tmp_path/'b', output_dir=tmp_path/'b'/'out', text='第一章\n另一份书。')
    a_summary = (a/'usage/cost_summary.json').read_bytes()
    (b/'usage/cost_summary.json').write_bytes(a_summary)
    assert read_valid_cost_summary(b, mb) is None
    assert job_status(str(b))['cost_summary'] is None
    refresh_cost_summary(b, mb)
    assert read_valid_cost_summary(b, mb)['job_identity']['job_id'] == mb.job_id
    before = (b/'usage/cost_summary.json').read_bytes()
    source = b/'source/input.txt'
    original_source = source.read_bytes()
    source.write_bytes(b'different source')
    assert read_valid_cost_summary(b, mb) is None
    with pytest.raises(BookCastError, match='来源哈希无法验证'):
        refresh_cost_summary(b, mb)
    source.write_bytes(original_source)
    mb.output_id = 'wrong-output'
    with pytest.raises(BookCastError, match='身份无法验证'):
        refresh_cost_summary(b, mb)
    assert (b/'usage/cost_summary.json').read_bytes() == before
    assert (a/'usage/cost_summary.json').read_bytes() == a_summary
    assert ma.job_id != mb.job_id


def test_old_job_without_creation_snapshot_cannot_claim_actual(tmp_path):
    root, manifest = create_job(tmp_path)
    manifest.cost_snapshot = None
    write_json(root/'manifest.json', manifest.model_dump(mode='json'))
    summary = refresh_cost_summary(root, manifest)
    assert summary['llm']['providers']['priced::deepseek-flash']['cost']['status'] == 'unavailable'
    assert summary['total']['status'] == 'unavailable'
    assert summary['total']['known_amount'] == 0
    assert job_status(str(root))['cost_summary'] == summary


def test_cost_failure_is_diagnostic_and_does_not_fail_generation(tmp_path, monkeypatch):
    def broken(*args):
        raise RuntimeError('secret upstream text must not appear in events')
    monkeypatch.setattr('bookcast.cost.refresh_cost_summary', broken)
    root, manifest = create_job(tmp_path)
    assert manifest.status == 'completed'
    events = (root/'logs/events.jsonl').read_text()
    assert 'cost_summary_diagnostic' in events
    assert 'RuntimeError' in events
    assert 'secret upstream text' not in events

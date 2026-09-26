"""Opt-in intent routing uses real chains and Core, with no network or paid models."""

import io
import json
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from bookcast.cli import app
from bookcast.composition import configured_pipeline, resume_pipeline, settings_snapshot
from bookcast.content_models import EvidenceAnalysis
from bookcast.cost import read_valid_cost_summary
from bookcast.errors import BookCastError
from bookcast.model_routing import RoutedLLMChain
from bookcast.pipeline import load_manifest
from bookcast.provider_api import ErrorKind, FAILOVER_ERRORS, ProviderError
from bookcast.provider_chain import ProviderChain
from bookcast.provider_config import LLMRouting, ProvidersConfig, load_config
from bookcast.provider_registry import default_registry
from bookcast.storage import fingerprint, sha256_file


EXAMPLE = Path(__file__).resolve().parents[1] / 'examples/model-routing-mock.toml'


def settings(**route_updates):
    # Names intentionally match target vendors; all implementations are offline Mock.
    routes = {'general': ['qwen'], 'cheap': ['qwen'], 'complex': ['deepseek'],
              'high_quality': ['deepseek'], **route_updates}
    return ProvidersConfig.model_validate({
        'providers': [
            {'name': 'qwen', 'kind': 'llm', 'type': 'mock', 'model': 'mock-general'},
            {'name': 'deepseek', 'kind': 'llm', 'type': 'mock', 'model': 'mock-complex'},
            {'name': 'voice', 'kind': 'tts', 'type': 'mock', 'model': 'mock-tones-v1'},
        ],
        'llm_priority': ['qwen', 'deepseek'], 'tts_priority': ['voice'], 'llm_routing': routes,
    })


def execute(chain, task, *, failure=None, invoked=None, journal=None):
    invoked = invoked if invoked is not None else []
    journal = journal if journal is not None else []

    def invoke(provider):
        invoked.append(provider.name)
        if failure and provider.name == 'qwen':
            raise failure
        return provider.name

    chain.execute(task=task, kind='llm', prompt_version='test', input_hash='a' * 64,
                  invoke=invoke, persist=lambda _: {'result.json': 'b' * 64},
                  observe=lambda attempt: journal.append(attempt.model_copy(deep=True)))
    return invoked, journal


@pytest.mark.parametrize('task,expected', [
    ('analysis:0001:0001', 'qwen'), ('synthesis/chapters/0001/00', 'qwen'),
    ('synthesis/book/00', 'deepseek'), ('script:0001', 'deepseek'),
    ('consistency:0001', 'deepseek'), ('future-operation', 'qwen'),
])
def test_task_intent_routes_without_vendor_logic_in_core(task, expected):
    chain = default_registry().chain(settings(), 'llm')
    assert isinstance(chain, RoutedLLMChain)
    invoked, history = execute(chain, task)
    assert invoked == [expected]
    assert history[-1].provider == expected
    assert history[-1].model == ('mock-general' if expected == 'qwen' else 'mock-complex')
    assert history[-1].provider_config_hash and history[-1].output_hash
    assert [attempt.status for attempt in history] == ['pending', 'running', 'completed']


def test_task_mapping_is_configurable_and_other_is_explicit():
    chain = default_registry().chain(settings(task_profiles={'extraction': 'complex', 'other': 'high_quality'}), 'llm')
    assert execute(chain, 'analysis:0001')[0] == ['deepseek']
    assert execute(chain, 'unknown-task')[0] == ['deepseek']


def test_explicit_override_bypasses_routing_and_auto_priority():
    config = settings()
    config.llm_priority = ['qwen']  # Explicit selection may select any configured instance.
    chain = default_registry().chain(config, 'llm', 'deepseek')
    assert type(chain) is ProviderChain
    assert execute(chain, 'analysis:0001')[0] == ['deepseek']
    with pytest.raises(BookCastError, match='未配置 llm'):
        default_registry().chain(settings(), 'llm', 'voice')
    with pytest.raises(BookCastError, match='未配置 llm'):
        default_registry().chain(settings(), 'llm', 'missing')


@pytest.mark.parametrize('update', [
    {'cheap': []}, {'complex': ['missing']}, {'general': ['voice']},
    {'general': ['qwen', 'qwen']}, {'cheap': ['not-enabled']},
    {'task_profiles': {'extraction': 'fast'}}, {'task_profiles': {'analysiss': 'cheap'}},
    {'priority': ['qwen']}, {'cheap': 'qwen'},
])
def test_invalid_routing_fails_before_construction(update):
    with pytest.raises(ValidationError):
        settings(**update)


def test_referenced_provider_must_be_enabled_and_all_profiles_required():
    raw = settings().model_dump(mode='json')
    raw['llm_priority'] = ['qwen']
    with pytest.raises(ValidationError):
        ProvidersConfig.model_validate(raw)
    raw = settings().model_dump(mode='json')
    del raw['llm_routing']['cheap']
    with pytest.raises(ValidationError):
        ProvidersConfig.model_validate(raw)
    raw['llm_routing'] = {}
    with pytest.raises(ValidationError):
        ProvidersConfig.model_validate(raw)


def test_config_errors_remain_sanitized(tmp_path):
    path = tmp_path / 'invalid.toml'
    path.write_text(EXAMPLE.read_text().replace('cheap = ["general-mock"]',
                                              'cheap = ["private-value-must-not-leak"]'))
    with pytest.raises(BookCastError) as failure:
        load_config(path)
    assert 'private-value-must-not-leak' not in str(failure.value)


@pytest.mark.parametrize('kind', sorted(FAILOVER_ERRORS))
def test_fallback_uses_only_the_explicit_route_and_keeps_attempts(kind):
    chain = default_registry().chain(settings(cheap=['qwen', 'deepseek']), 'llm')
    invoked, history = execute(chain, 'analysis:0001', failure=ProviderError(kind))
    assert invoked == ['qwen', 'deepseek']
    assert [a.status for a in history] == ['pending', 'running', 'failed_retryable',
                                         'pending', 'running', 'completed']
    assert chain.statuses['qwen'].last_error == kind
    # Stickiness is local to cheap, not to all profiles that happen to share qwen.
    assert execute(chain, 'analysis:0002')[0] == ['deepseek']
    assert execute(chain, 'synthesis/chapters/0002/00')[0] == ['qwen']


def test_no_implicit_fallback_to_priority_pool_or_when_disabled():
    for config in (settings(), settings(cheap=['qwen', 'deepseek'])):
        if len(config.llm_routing.cheap) > 1:
            config.failover_on = []
        chain = default_registry().chain(config, 'llm')
        invoked = []
        with pytest.raises(ProviderError) as failure:
            execute(chain, 'analysis:0001', failure=ProviderError(ErrorKind.TIMEOUT), invoked=invoked)
        assert failure.value.kind == ErrorKind.TIMEOUT
        assert invoked == ['qwen']


@pytest.mark.parametrize('kind', [ErrorKind.AUTH, ErrorKind.PERMISSION, ErrorKind.INPUT,
                                 ErrorKind.SCHEMA, ErrorKind.BUSINESS])
def test_permanent_failures_never_route_to_backup(kind):
    chain = default_registry().chain(settings(cheap=['qwen', 'deepseek']), 'llm')
    history, invoked = [], []
    with pytest.raises(ProviderError):
        execute(chain, 'analysis:0001', failure=ProviderError(kind), invoked=invoked, journal=history)
    assert invoked == ['qwen'] and history[-1].status == 'failed_permanent'


def test_restore_keeps_each_profile_independent_and_can_reprobe():
    config = settings(cheap=['qwen', 'deepseek'])
    chain = default_registry().chain(config, 'llm')
    journal = []
    execute(chain, 'analysis:0001', failure=ProviderError(ErrorKind.QUOTA), journal=journal)
    execute(chain, 'synthesis/chapters/0001/00', journal=journal)
    restored = default_registry().chain(config, 'llm')
    restored.restore(journal, 'llm')
    assert execute(restored, 'analysis:0002')[0] == ['deepseek']
    assert execute(restored, 'synthesis/chapters/0002/00')[0] == ['qwen']
    assert restored.routes['cheap'].disabled == set()
    # Failed primary alone restores the backup position, as the original chain does.
    restored.restore([a for a in journal if a.status == 'failed_retryable'], 'llm')
    assert execute(restored, 'analysis:0002')[0] == ['deepseek']


def test_legacy_serialization_chain_identity_and_tts_unchanged():
    raw = settings().model_dump(mode='json')
    raw.pop('llm_routing')
    legacy = ProvidersConfig.model_validate(raw)
    assert legacy.model_dump(mode='json') == raw
    assert json.loads(legacy.model_dump_json()) == raw
    assert legacy.model_dump() == raw
    assert ProvidersConfig.model_validate({**raw, 'llm_routing': None}).model_dump(mode='json') == raw
    assert 'llm_routing' not in settings_snapshot(legacy, 'auto', 'auto')['config']
    registry = default_registry()
    chain = registry.chain(legacy, 'llm')
    assert type(chain) is ProviderChain
    assert chain.cache_key == fingerprint([(p.name, p.model, p.cache_key) for p in chain.providers])
    assert execute(chain, 'synthesis/book/00')[0] == ['qwen']
    assert type(registry.chain(settings(), 'tts')) is ProviderChain
    assert registry.chain(settings(), 'tts').cache_key == registry.chain(legacy, 'tts').cache_key


def test_deepseek_bounded_schema_retry_and_usage_survive_routing(monkeypatch):
    config = settings(cheap=['deepseek', 'qwen'])
    raw = config.model_dump(mode='json')
    raw['providers'][1].update(type='openai-compatible', model='deepseek-flash', base_url='https://api.deepseek.com')
    config = ProvidersConfig.model_validate(raw)
    valid = {'chapter_id': '0001', 'chunk_id': '0001', 'is_mock': False,
             'core_ideas': [{'text': '简短转述', 'evidence_id': 'e0001'}],
             **{name: [] for name in ('arguments', 'evidence', 'examples', 'people', 'concepts',
                                      'counter_arguments', 'connections', 'key_passages')}}
    sent = []

    class Transport:
        def open(self, req, timeout):
            sent.append(json.loads(req.data))
            result = dict(valid)
            if len(sent) == 1:
                result['evidence'] = valid['core_ideas'] * 7
            return io.BytesIO(json.dumps({'choices': [{'message': {'content': json.dumps(result)},
                'finish_reason': 'stop'}], 'model': 'deepseek-reported',
                'usage': {'prompt_tokens': 80, 'completion_tokens': 40}}).encode())

    monkeypatch.setattr('bookcast.adapters.compatible.request.build_opener', lambda *args: Transport())
    history = []
    default_registry().chain(config, 'llm').execute(
        task='analysis:0001:0001', kind='llm', prompt_version='test', input_hash='a' * 64,
        invoke=lambda p: p.generate_structured('bounded payload', EvidenceAnalysis),
        persist=lambda _: {'result.json': 'b' * 64}, observe=lambda a: history.append(a.model_copy(deep=True)))
    terminal = [a for a in history if a.status in {'completed', 'failed_retryable'}]
    assert len(sent) == 2
    assert [a.status for a in terminal] == ['failed_retryable', 'completed']
    assert all(a.provider == 'deepseek' and a.reported_model == 'deepseek-reported' for a in terminal)
    assert all(a.provider_reported_usage.input_tokens == 80 for a in terminal)
    assert 'at most 6 items' in sent[1]['messages'][0]['content']


def test_pipeline_snapshot_cost_and_resume_without_new_calls(tmp_path, monkeypatch):
    config = settings()
    source = tmp_path / 'book.txt'
    source.write_text('第一章\n协作有收益。\n第二章\n分工也有成本。')
    root = configured_pipeline(config, tmp_path / 'output').generate(source, minutes=1)
    manifest = load_manifest(root / 'manifest.json')
    assert manifest.status == 'completed'
    calls = [a for a in manifest.ai_calls if a.kind == 'llm']
    assert {a.provider for a in calls} == {'qwen', 'deepseek'}
    assert all(a.provider == ('qwen' if config.llm_routing.profile_for_task(a.task) in {'cheap', 'general'}
                              else 'deepseek') for a in calls)
    usage = json.loads((root / 'usage/llm_usage.json').read_text())
    assert usage['total']['request_count'] == len(calls)
    summary = read_valid_cost_summary(root, manifest)
    assert set(summary['llm']['providers']) == {'qwen::mock-general', 'deepseek::mock-complex'}
    assert summary['total']['status'] != 'actual'  # No price/usage is never invented.
    assert manifest.cost_snapshot['config']['llm_routing'] == config.llm_routing.model_dump(mode='json')
    before = (root / 'manifest.json').read_bytes()
    audio_hash = sha256_file(root / 'podcast.mp3')
    # Resume reads the saved configuration, even with an invalid CWD config.
    (tmp_path / 'bookcast.toml').write_text('not-valid-toml')
    monkeypatch.chdir(tmp_path)
    resume_pipeline(manifest, root, None, None, None).resume_job(root)
    assert (root / 'manifest.json').read_bytes() == before
    assert sha256_file(root / 'podcast.mp3') == audio_hash
    # A changed route affects future work; D-014 keeps completed content.
    changed = settings(cheap=['deepseek'], general=['deepseek'])
    configured_pipeline(changed, root.parent).resume_job(root)
    current = load_manifest(root / 'manifest.json')
    assert [a.id for a in current.ai_calls] == [a.id for a in manifest.ai_calls]
    assert sha256_file(root / 'podcast.mp3') == audio_hash


def test_cli_example_is_offline_and_explicit_override_wins(tmp_path):
    runner = CliRunner()
    result = runner.invoke(app, ['config', 'providers', '--config', str(EXAMPLE)])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)['llm_routing']['cheap'] == ['general-mock']
    source = tmp_path / 'book.txt'
    source.write_text('第一章\n协作有收益。')
    result = runner.invoke(app, ['generate', str(source), '--config', str(EXAMPLE), '--minutes', '1',
                                 '--provider', 'complex-mock', '--output-dir', str(tmp_path / 'out')])
    assert result.exit_code == 0, result.output
    manifest = load_manifest(next((tmp_path / 'out').glob('*/manifest.json')))
    assert {a.provider for a in manifest.ai_calls if a.kind == 'llm'} == {'complex-mock'}


def test_web_worker_uses_saved_routing_without_public_api_changes(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from bookcast.web_api import create_app
    from bookcast.web_worker import run_worker

    application = create_app(tmp_path / 'web', config=EXAMPLE)
    service = application.state.service
    monkeypatch.setattr(service, 'launch', lambda identifier: None)
    with TestClient(application, base_url='http://127.0.0.1') as client:
        upload = client.post('/api/uploads', params={'filename': 'book.txt'},
                             content='第一章\n协作有收益。'.encode(), headers={'content-type': 'text/plain'})
        assert upload.status_code == 201
        identifier = uuid4().hex
        response = client.post('/api/jobs', headers={'Idempotency-Key': identifier},
                               json={'upload_id': upload.json()['upload_id'], 'minutes': 1})
        assert response.status_code == 202, response.text
        run_worker(service.root, identifier)
        status = client.get(f'/api/jobs/{identifier}').json()
        assert status['state'] == 'SUCCEEDED', status
        manifest = load_manifest(service.core_path(identifier))
        assert {a.provider for a in manifest.ai_calls if a.kind == 'llm'} == {'general-mock', 'complex-mock'}
        assert manifest.provider_settings['config']['llm_routing']

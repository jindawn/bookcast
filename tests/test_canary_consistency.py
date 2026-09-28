"""Offline Phase 19.3D contracts; no live provider or audio generation."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from bookcast.canary_consistency import canary_summary
from bookcast.content import ContentFlow
from bookcast.content_models import (ClaimReview, ConsistencyReview, ContentOptions,
                                     ContentTurn, EpisodePlan, Segment, SegmentScript)
from bookcast.generation import ProviderUsage
from bookcast.models import AIAttempt, Chapter, Manifest
from bookcast.pipeline import Pipeline, _Runner
from bookcast.model_routing import RoutedLLMChain
from bookcast.provider_api import ProviderError
from bookcast.provider_config import LLMRouting, ProvidersConfig
from bookcast.providers import MockLLMProvider, MockTTSProvider
from bookcast.provider_chain import ProviderChain
from bookcast.quality import evaluate
from bookcast.shadow_consistency import (Tier1ScreeningResult, compare_canary_reviews,
                                         merge_tier_reviews)


def script():
    return SegmentScript(segment_id='0001', title='片段', turns=[
        ContentTurn(speaker='主持人', intent='explain', text='来源事实甲', claim_ids=['c1'], attribution='source'),
        ContentTurn(speaker='嘉宾', intent='question', text='讨论', claim_ids=[], attribution='discussion'),
        ContentTurn(speaker='主持人', intent='explain', text='来源事实乙', claim_ids=['c1'], attribution='source'),
    ])


def segment():
    return Segment(id='0001', title='片段', chapter_ids=['0001'], claim_ids=['c1'], seconds=60,
                   target_chars=120, previous_topic='', next_topic='')


class FakeProvider(MockLLMProvider):
    def __init__(self, name, response):
        self.name, self.response, self.requests = name, response, []

    def generate_structured(self, request, model):
        self.requests.append(json.loads(request))
        value = self.response(self.requests[-1]) if callable(self.response) else self.response
        if isinstance(value, Exception):
            raise value
        return model.model_validate(value)


class Runner:
    def __init__(self, root: Path, tier1, deepseek):
        self.root, self.tier1, self.deepseek = root, tier1, deepseek
        self.manifest = SimpleNamespace(segment_revisions={}, ai_calls=[], cost_snapshot=None)
        self.events = []

    def path(self, path):
        return self.root / path

    def step(self, name, inputs, operation):
        operation()

    def ai_operation(self, name, kind, version, inputs, invoke):
        return invoke(self.tier1 if ':tier1:' in name else self.deepseek)

    def event(self, *args, **kwargs):
        self.events.append((args, kwargs))


def flow(tmp_path, tier1, deepseek, *, audit=False):
    result = ContentFlow.__new__(ContentFlow)
    result.r = Runner(tmp_path, tier1, deepseek)
    result.options = ContentOptions(consistency_mode='two_tier', consistency_canary_audit=audit)
    return result


def checks(*items):
    return {'segment_id': '0001', 'checks': [
        {'turn_index': i, 'verdict': verdict, 'reason': 'fixture'} for i, verdict in items]}


def test_defaults_and_legacy_options_are_full():
    assert ContentOptions().consistency_mode == 'full'
    assert ContentOptions.model_validate({'mode': 'two_host', 'minutes': 3}).consistency_mode == 'full'
    with pytest.raises(ValueError):
        ContentOptions(consistency_canary_audit=True)
    with pytest.raises(ValueError):
        ContentOptions(consistency_mode='two_tier', consistency_shadow_mode=True)


def test_two_tier_pass_uses_only_qwen_and_full_default_is_unchanged(tmp_path):
    qwen = FakeProvider('qwen', {'status': 'PASS', 'suspicious_turn_ids': [], 'reasons': []})
    deepseek = FakeProvider('deepseek', checks((0, 'contradicted'), (2, 'supported')))
    engine = flow(tmp_path, qwen, deepseek)
    result = engine._consistency_review(segment(), script(), {'c1': {'quote': '证据甲'}}, 0, lambda _: None)
    assert len(qwen.requests) == 1 and not deepseek.requests
    assert [c.verdict for c in result.checks] == ['supported', 'supported']
    assert qwen.requests[0]['turns'][0]['turn_index'] == 0
    assert len(qwen.requests[0]['turns']) == 2
    assert engine.r.path('evaluation/segments/0001.json').is_file()
    assert not engine.r.path('evaluation/canary').exists()


def test_full_mode_preserves_existing_task_and_prompt(tmp_path):
    provider = FakeProvider('deepseek', checks((0, 'supported'), (2, 'supported')))
    engine = flow(tmp_path, provider, provider)
    engine.options = ContentOptions()
    tasks = []
    original = engine.r.ai_operation
    def record(name, kind, version, inputs, invoke):
        tasks.append(name)
        return original(name, kind, version, inputs, invoke)
    engine.r.ai_operation = record
    review = engine._consistency_review(segment(), script(), {'c1': {'quote': '证据甲'}}, 0, lambda _: None)
    assert tasks == ['consistency:0001']
    assert provider.requests[0]['operation'] == 'consistency'
    assert [c.verdict for c in review.checks] == ['supported', 'supported']


def test_tier1_routing_is_cheap_without_changing_full_routing():
    chain = RoutedLLMChain([FakeProvider('qwen', {}), FakeProvider('deepseek', {})],
                           LLMRouting(general=['qwen'], cheap=['qwen'], complex=['deepseek'],
                                      high_quality=['deepseek']))
    assert chain._profile_for_task('consistency:tier1:0001') == 'cheap'
    assert chain._profile_for_task('consistency:tier2:0001') == 'high_quality'
    assert chain._profile_for_task('consistency:audit:0001') == 'high_quality'
    assert chain._profile_for_task('consistency:0001') == 'high_quality'


def test_real_attempt_journal_and_cache_are_separate_for_tiers_and_audit(tmp_path):
    qwen = FakeProvider('qwen', {'status': 'REVIEW', 'suspicious_turn_ids': [2], 'reasons': ['risk']})
    deepseek = FakeProvider('deepseek', lambda payload: checks((2, 'contradicted'))
                            if len(payload['script']['turns']) == 1 else checks((0, 'supported'), (2, 'supported')))
    llm = RoutedLLMChain([qwen, deepseek], LLMRouting(general=['qwen'], cheap=['qwen'],
                         complex=['deepseek'], high_quality=['deepseek']))
    voice = ProviderChain([MockTTSProvider()])
    manifest = Manifest(book_id='book', source_sha256='a' * 64, source_name='test.txt',
                        source_format='txt', config={'llm': llm.cache_key, 'tts': voice.cache_key},
                        pipeline_version='2', content_options=ContentOptions(
                            consistency_mode='two_tier', consistency_canary_audit=True).model_dump())
    runner = _Runner(tmp_path, manifest, llm, voice)
    engine = ContentFlow.__new__(ContentFlow)
    engine.r, engine.options = runner, ContentOptions(consistency_mode='two_tier', consistency_canary_audit=True)
    evidence = {'c1': {'quote': '证据甲'}}
    review = engine._consistency_review(segment(), script(), evidence, 0, lambda _: None)
    assert review.checks[1].verdict == 'contradicted'
    assert [call.task for call in manifest.ai_calls] == ['consistency:tier1:0001', 'consistency:tier2:0001']
    engine._consistency_review(segment(), script(), evidence, 0, lambda _: None)
    assert len(manifest.ai_calls) == 2 and len(qwen.requests) == len(deepseek.requests) == 1
    engine._run_canary_audit(segment(), script(), evidence, 0, review)
    assert len(manifest.ai_calls) == 3 and manifest.ai_calls[-1].task == 'consistency:audit:0001'
    record = json.loads((tmp_path / 'evaluation/canary/0001.json').read_text())
    assert record['potential_false_negative'] is False


def test_failed_audit_does_not_disable_production_deepseek_route(tmp_path):
    qwen = FakeProvider('qwen', {'status': 'REVIEW', 'suspicious_turn_ids': [2]})
    deepseek = FakeProvider('deepseek', checks((2, 'supported')))
    llm = RoutedLLMChain([qwen, deepseek], LLMRouting(general=['qwen'], cheap=['qwen'],
                         complex=['deepseek'], high_quality=['deepseek']))
    voice = ProviderChain([MockTTSProvider()])
    manifest = Manifest(book_id='book', source_sha256='a' * 64, source_name='test.txt',
                        source_format='txt', config={'llm': llm.cache_key, 'tts': voice.cache_key},
                        pipeline_version='2', content_options=ContentOptions(
                            consistency_mode='two_tier', consistency_canary_audit=True).model_dump())
    runner = _Runner(tmp_path, manifest, llm, voice)
    engine = ContentFlow.__new__(ContentFlow)
    engine.r, engine.options = runner, ContentOptions(consistency_mode='two_tier', consistency_canary_audit=True)
    evidence = {'c1': {'quote': '证据甲'}}
    production = engine._consistency_review(segment(), script(), evidence, 0, lambda _: None)
    deepseek.response = RuntimeError('audit unavailable')
    engine._run_canary_audit(segment(), script(), evidence, 0, production)
    assert not llm.routes['high_quality'].disabled
    deepseek.response = checks((2, 'supported'))
    assert engine._consistency_review(segment(), script(), evidence, 1, lambda _: None).checks[1].verdict == 'supported'


def test_two_tier_review_targets_only_suspicious_turn_and_merges(tmp_path):
    qwen = FakeProvider('qwen', {'status': 'REVIEW', 'suspicious_turn_ids': [2], 'reasons': ['risk']})
    deepseek = FakeProvider('deepseek', checks((2, 'contradicted')))
    engine = flow(tmp_path, qwen, deepseek)
    result = engine._consistency_review(segment(), script(), {'c1': {'quote': '证据甲'}}, 0, lambda _: None)
    assert len(qwen.requests) == len(deepseek.requests) == 1
    assert [turn['turn_index'] for turn in deepseek.requests[0]['script']['turns']] == [2]
    assert len(deepseek.requests[0]['claims']) == 1
    assert [(c.turn_index, c.verdict) for c in result.checks] == [(0, 'supported'), (2, 'contradicted')]
    plan = EpisodePlan(mode='summary', budget_seconds=60, segments=[segment()],
                       covered_chapters=['0001'], omitted_chapters=[], deduplicated_themes=0)
    chapter = Chapter(id='0001', title='章节', text='源文证据甲', source_locator='local')
    report = evaluate(plan, [script()], {'c1': {'quote': '源文证据甲', 'chapter_id': '0001'}},
                      [chapter], [result])
    assert any('一致性' in issue for issue in report['blocking_issues'])


@pytest.mark.parametrize('tier2', [None, ConsistencyReview.model_validate(checks((0, 'supported')))])
def test_tier2_missing_or_wrong_turn_fails_closed(tier2):
    with pytest.raises(ProviderError):
        merge_tier_reviews('0001', script(), Tier1ScreeningResult(status='REVIEW',
                           suspicious_turn_ids=[2]), tier2)


def test_canary_audit_does_not_override_production_and_failure_is_indeterminate(tmp_path):
    qwen = FakeProvider('qwen', {'status': 'PASS'})
    deepseek = FakeProvider('deepseek', checks((0, 'contradicted'), (2, 'supported')))
    engine = flow(tmp_path, qwen, deepseek, audit=True)
    production = engine._consistency_review(segment(), script(), {'c1': {'quote': '证据甲'}}, 0, lambda _: None)
    engine._run_canary_audit(segment(), script(), {'c1': {'quote': '证据甲'}}, 0, production)
    record = json.loads(engine.r.path('evaluation/canary/0001.json').read_text())
    assert record['potential_false_negative'] is True
    assert record['audit_only_warning'] == [0]
    assert [c.verdict for c in production.checks] == ['supported', 'supported']
    deepseek.response = RuntimeError('offline audit failure')
    engine._run_canary_audit(segment(), script(), {'c1': {'quote': '证据甲'}}, 1, production)
    record = json.loads(engine.r.path('evaluation/canary/0001.json').read_text())
    assert record['audit_status'] == 'failed' and record['potential_false_negative'] is None
    assert [c.verdict for c in production.checks] == ['supported', 'supported']


def test_canary_comparison_requires_complete_audit_checks():
    production = ConsistencyReview.model_validate(checks((0, 'supported'), (2, 'supported')))
    incomplete = ConsistencyReview.model_validate(checks((0, 'supported')))
    row = compare_canary_reviews(production, incomplete, {0, 2})
    assert row['audit_status'] == 'indeterminate'
    assert row['potential_false_negative'] is None


def test_comparison_uses_turn_risks_in_both_directions():
    prod = ConsistencyReview.model_validate(checks((0, 'contradicted'), (2, 'supported')))
    audit = ConsistencyReview.model_validate(checks((0, 'supported'), (2, 'unverifiable')))
    row = compare_canary_reviews(prod, audit, {0, 2})
    assert row['potential_false_negative'] is True
    assert row['audit_only_warning'] == [2]
    assert row['two_tier_only_warning'] == [0]
    assert compare_canary_reviews(prod, None, {0, 2})['potential_false_negative'] is None


def test_canary_price_counts_each_attempt_once_even_with_physical_telemetry(tmp_path):
    config = ProvidersConfig.model_validate({
        'providers': [{'name': 'qwen', 'kind': 'llm', 'type': 'mock', 'model': 'qwen-model'},
                      {'name': 'deepseek', 'kind': 'llm', 'type': 'mock', 'model': 'deepseek-model'},
                      {'name': 'voice', 'kind': 'tts', 'type': 'mock', 'model': 'voice'}],
        'llm_priority': ['qwen', 'deepseek'], 'tts_priority': ['voice'],
        'pricing': {'qwen-model': {'default': {'uncached_input_per_million': 1, 'cached_input_per_million': 0.5,
                                              'output_per_million': 2}},
                    'deepseek-model': {'default': {'uncached_input_per_million': 2, 'cached_input_per_million': 1,
                                                  'output_per_million': 4}}}})
    calls = []
    for task, provider, model in [('consistency:tier1:0001', 'qwen', 'qwen-model'),
                                   ('consistency:audit:0001', 'deepseek', 'deepseek-model')]:
        calls.append(AIAttempt(id=task, task=task, kind='llm', provider=provider, model=model, status='completed',
                    prompt_version='content-v1', input_hash='a' * 64,
                    provider_reported_usage=ProviderUsage(input_tokens=1000, output_tokens=100,
                                                           cache_hit_tokens=0, reasoning_tokens=20)))
    snapshot = {'config': config.model_dump(mode='json'), 'timezone': 'Asia/Shanghai',
                'pricing_policy': 'cn-workday-peak-v1'}
    telemetry = tmp_path / 'physical.jsonl'
    telemetry.write_text(''.join(json.dumps({'logical_chunk_id': call.task, 'latency': 1.2}) + '\n'
                                 for call in calls))
    row = {'audit_status': 'completed', 'potential_false_negative': False}
    first = canary_summary(SimpleNamespace(ai_calls=calls, cost_snapshot=snapshot), [row], telemetry)
    telemetry.write_text(telemetry.read_text() * 2)
    second = canary_summary(SimpleNamespace(ai_calls=calls, cost_snapshot=snapshot), [row], telemetry)
    assert first['actual_canary_experiment_cost'] == second['actual_canary_experiment_cost']
    assert first['full_counterfactual_cost'] > first['two_tier_production_cost'] > 0


def test_mode_is_immutable_on_resume_without_running_content(tmp_path, monkeypatch):
    source = tmp_path / 'input.txt'
    source.write_text('测试内容。')
    monkeypatch.setattr('bookcast.pipeline._Runner.run', lambda self, *args, **kwargs: None)
    pipeline = Pipeline(MockLLMProvider(), MockTTSProvider(), tmp_path / 'out')
    root = pipeline.generate(source, consistency_mode='two_tier', consistency_canary_audit=True)
    assert pipeline.generate(source, resume=True) == root
    from bookcast.pipeline import load_manifest
    assert load_manifest(root / 'manifest.json').content_options['consistency_mode'] == 'two_tier'
    from bookcast.errors import BookCastError
    with pytest.raises(BookCastError):
        pipeline.generate(source, resume=True, consistency_mode='full')


def test_new_job_defaults_two_tier_but_legacy_and_existing_modes_are_stable(tmp_path, monkeypatch):
    from bookcast.pipeline import load_manifest
    monkeypatch.setattr('bookcast.pipeline._Runner.run', lambda self, *args, **kwargs: None)
    source = tmp_path / 'input.txt'
    source.write_text('测试内容。')
    pipeline = Pipeline(MockLLMProvider(), MockTTSProvider(), tmp_path / 'out')

    new = pipeline.generate(source)
    created = load_manifest(new / 'manifest.json')
    assert created.content_options['consistency_mode'] == 'two_tier'
    assert created.content_options['consistency_canary_audit'] is False
    assert pipeline.generate(source, resume=True) == new
    assert load_manifest(new / 'manifest.json').content_options['consistency_mode'] == 'two_tier'

    full = Pipeline(MockLLMProvider(), MockTTSProvider(), tmp_path / 'full').generate(
        source, consistency_mode='full')
    assert ContentOptions.model_validate(load_manifest(full / 'manifest.json').content_options).consistency_mode == 'full'
    assert Pipeline(MockLLMProvider(), MockTTSProvider(), tmp_path / 'full').generate(source, resume=True) == full
    assert ContentOptions.model_validate(load_manifest(full / 'manifest.json').content_options).consistency_mode == 'full'

    legacy_manifest = load_manifest(full / 'manifest.json')
    legacy_manifest.content_options.pop('consistency_mode', None)
    from bookcast.storage import write_json
    write_json(full / 'manifest.json', legacy_manifest.model_dump())
    assert Pipeline(MockLLMProvider(), MockTTSProvider(), tmp_path / 'full').generate(source, resume=True) == full
    assert ContentOptions.model_validate(load_manifest(full / 'manifest.json').content_options).consistency_mode == 'full'


def test_new_job_full_override_and_shadow_flag_keep_full(tmp_path, monkeypatch):
    from bookcast.pipeline import load_manifest
    monkeypatch.setattr('bookcast.pipeline._Runner.run', lambda self, *args, **kwargs: None)
    source = tmp_path / 'input.txt'
    source.write_text('测试内容。')
    for label, flags in [('rollback', {'consistency_mode': 'full'}),
                         ('legacy_shadow', {'consistency_shadow_mode': True})]:
        root = Pipeline(MockLLMProvider(), MockTTSProvider(), tmp_path / label).generate(source, **flags)
        options = ContentOptions.model_validate(load_manifest(root / 'manifest.json').content_options)
        assert options.consistency_mode == 'full'
        assert options.consistency_canary_audit is False


def test_cli_exposes_distinct_shadow_and_canary_options():
    from typer.testing import CliRunner
    from bookcast.cli import app
    result = CliRunner().invoke(app, ['generate', '--help'])
    assert result.exit_code == 0
    assert '--shadow-consist' in result.output
    assert '--consistency-mo' in result.output
    assert '--consistency-ca' in result.output


def test_cli_overrides_new_job_without_generating_audio(tmp_path, monkeypatch):
    from typer.testing import CliRunner
    from bookcast.cli import app
    output = tmp_path / 'job'
    (output / 'audio').mkdir(parents=True)
    (output / 'audio/export.json').write_text('{}')
    received = []
    class StubPipeline:
        def generate(self, *args, **kwargs):
            received.append(kwargs)
            return output
    monkeypatch.setattr('bookcast.cli.generation_pipeline', lambda *args, **kwargs: StubPipeline())
    monkeypatch.setattr('bookcast.cli.load_manifest', lambda *args: SimpleNamespace(
        pipeline_version='1', warnings=[], job_id='offline'))
    result = CliRunner().invoke(app, ['generate', str(tmp_path / 'source.txt'),
                                      '--consistency-mode', 'two-tier', '--consistency-canary-audit'])
    assert result.exit_code == 0
    assert received[0]['consistency_mode'] == 'two_tier'
    assert received[0]['consistency_canary_audit'] is True


@pytest.mark.parametrize(('flag', 'expected'), [('full', 'full'), ('two-tier', 'two_tier')])
def test_cli_mode_override_without_audit(tmp_path, monkeypatch, flag, expected):
    from typer.testing import CliRunner
    from bookcast.cli import app
    output = tmp_path / 'job'
    (output / 'audio').mkdir(parents=True)
    (output / 'audio/export.json').write_text('{}')
    received = []
    class StubPipeline:
        def generate(self, *args, **kwargs):
            received.append(kwargs)
            return output
    monkeypatch.setattr('bookcast.cli.generation_pipeline', lambda *args, **kwargs: StubPipeline())
    monkeypatch.setattr('bookcast.cli.load_manifest', lambda *args: SimpleNamespace(
        pipeline_version='1', warnings=[], job_id='offline'))
    result = CliRunner().invoke(app, ['generate', str(tmp_path / 'source.txt'),
                                      '--consistency-mode', flag])
    assert result.exit_code == 0
    assert received[0]['consistency_mode'] == expected
    assert received[0]['consistency_canary_audit'] is None

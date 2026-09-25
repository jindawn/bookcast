"""Offline protocol fixtures, never real model output or reported live billing."""
import io
import json
from pathlib import Path
from urllib.error import HTTPError

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from bookcast.adapters.compatible import CompatibleLLMProvider, classify_http
from bookcast.cli import app
from bookcast.content_models import EvidenceAnalysis, Synthesis, SegmentScript, ConsistencyReview
from bookcast.generation import GenerationConfig, ProviderUsage, resolve_generation
from bookcast.llm_usage import usage_snapshot
from bookcast.models import AIAttempt
from bookcast.pipeline import Pipeline, load_manifest
from bookcast.provider_api import ErrorKind, ProviderError
from bookcast.provider_chain import ProviderChain, provider_config_hash
from bookcast.provider_config import ProviderSpec, load_config
from bookcast.providers import MockLLMProvider, MockTTSProvider
from bookcast.storage import fingerprint, sha256_file


def spec(**overrides):
    return ProviderSpec.model_validate(dict(name='A', kind='llm', type='openai-compatible',
        model='deepseek-flash', base_url='https://example.invalid', **overrides))


@pytest.mark.parametrize('options', [
    {'thinking': True}, {'thinking': 'auto'}, {'reasoning_effort': 99}, {'reasoning_effort': 'ultra'},
    {'thinking': 'disabled', 'reasoning_effort': 'low'}, {'max_tokens': True}, {'max_tokens': '200'},
    {'max_tokens': 65537}, {'max_tokens': 0}, {'extra_body': {'Authorization': 'secret'}},
    {'api_key': 'secret'}, {'thinking': 'x'*100000},
])
def test_options_are_bounded_and_strict(options):
    with pytest.raises(ValidationError):
        GenerationConfig.model_validate(options)


def test_task_policy_and_explicit_override():
    expected = {'analysis:0001:0001': ('extraction', 'disabled', None),
                'synthesis/chapters/0001/00-0000': ('chapter_synthesis', 'disabled', None),
                'synthesis/book/00-0000': ('book_synthesis', 'enabled', 'high'),
                'script:0001': ('dialogue', 'enabled', 'low'),
                'consistency:0001': ('consistency', 'enabled', 'low')}
    for task, (kind, thinking, effort) in expected.items():
        audit = resolve_generation(task, 'bookcast-v1', None)
        assert (audit.task_type, audit.options.thinking, audit.options.reasoning_effort) == (kind, thinking, effort)
        disabled = resolve_generation(task, 'bookcast-v1', GenerationConfig(thinking='disabled'))
        assert disabled.options.request_fields() == {'thinking': {'type': 'disabled'},
                                                      'max_tokens': audit.options.max_tokens}
        low = resolve_generation(task, 'bookcast-v1', GenerationConfig(reasoning_effort='low'))
        assert low.options.thinking == 'enabled' and low.options.reasoning_effort == 'low'
    assert resolve_generation('future-task', 'bookcast-v1', None).options.request_fields() == {}


def test_cache_compatibility_and_task_scope():
    old = CompatibleLLMProvider(spec())
    assert old.cache_key == fingerprint({'type': 'openai-compatible', 'model': 'deepseek-flash',
                                         'endpoint': 'https://example.invalid'})
    assert provider_config_hash(old) == provider_config_hash(old, 'analysis:0001:0001')
    auto = CompatibleLLMProvider(spec(reasoning_policy='bookcast-v1'))
    disabled = CompatibleLLMProvider(spec(reasoning_policy='bookcast-v1', generation={'thinking': 'disabled'}))
    assert provider_config_hash(auto, 'analysis:1') == provider_config_hash(disabled, 'analysis:1')
    assert provider_config_hash(auto, 'synthesis/book/0') != provider_config_hash(disabled, 'synthesis/book/0')
    assert auto.cache_key != disabled.cache_key
    rotated = CompatibleLLMProvider(spec(api_key_env='ANOTHER_KEY', timeout_seconds=100))
    assert provider_config_hash(rotated, 'analysis:1') == provider_config_hash(old, 'analysis:1')


@pytest.mark.parametrize('status,kind', [(400,'input_error'), (401,'authentication_error'),
    (402,'quota_exhausted'), (422,'input_error'), (429,'rate_limit'), (408,'timeout'),
    (500,'temporary_unavailable'), (503,'temporary_unavailable'), (504,'timeout')])
@pytest.mark.parametrize('body', [b'private service text', b'[]', b'{"error":{"code":[],"message":"secret"}}'])
def test_documented_http_status_without_body_dependency(status, kind, body):
    result = classify_http(status, body)
    assert result.kind == kind and 'secret' not in str(result)


def test_usage_unknown_and_strict_counts():
    assert ProviderUsage.from_response(None) is None
    assert ProviderUsage.from_response([]) is None
    assert set(ProviderUsage.from_response({}).model_dump().values()) == {None}
    usage = ProviderUsage.from_response({'prompt_tokens': True, 'completion_tokens': '5',
        'completion_tokens_details': {'reasoning_tokens': -1}, 'prompt_cache_hit_tokens': 2**64})
    assert set(usage.model_dump().values()) == {None}
    assert ProviderUsage.from_response({'prompt_tokens_details': {'cached_tokens': 0}}).cache_hit_tokens == 0


def test_stage_usage_telemetry_counts_failures_reuse_and_optional_cost():
    base = dict(kind='llm', provider='deepseek', model='deepseek-flash',
                prompt_version='v1', input_hash='hash')
    calls = [
        AIAttempt(id='one', task='analysis:0001:0001', status='completed',
                  provider_reported_usage=ProviderUsage(input_tokens=100, cache_hit_tokens=20,
                                                        output_tokens=40, reasoning_tokens=10), **base),
        AIAttempt(id='two', task='synthesis/chapters/0001/00-0000', status='failed_permanent',
                  provider_reported_usage=ProviderUsage(input_tokens=30, output_tokens=50), **base),
    ]
    usage = usage_snapshot(calls, {'extraction': 2},
                           {('deepseek', 'deepseek-flash'): {'input': 1, 'cached_input': .2, 'output': 4}})
    assert usage['total']['request_count'] == 2
    assert usage['total']['input_tokens'] == 130
    assert usage['total']['cached_input_tokens'] == 20
    assert usage['total']['output_tokens'] == 90  # reasoning is included in output
    assert usage['total']['cache_reuse_count'] == 2
    assert usage['by_stage']['chapter_synthesis']['request_count'] == 1
    assert usage['total']['estimated_cost'] == round((110 + 20*.2 + 90*4)/1_000_000, 6)
    assert usage_snapshot(calls)['total']['estimated_cost'] is None


def test_policy_stage_budgets_bound_broad_provider_override():
    override = GenerationConfig(max_tokens=16384)
    assert resolve_generation('analysis:0001:0001', 'bookcast-v1', override).options.max_tokens == 16384
    assert resolve_generation('synthesis/chapters/0001/00-0000', 'bookcast-v1', override).options.max_tokens == 4096
    assert resolve_generation('script:0001', 'bookcast-v1', override).options.max_tokens == 12000
    assert resolve_generation('consistency:0001', 'bookcast-v1', override).options.max_tokens == 12000


def test_mock_job_emits_usage_file_without_prompt_or_secret(tmp_path):
    source = Path(__file__).parents[1]/'examples/content-demo.txt'
    job = Pipeline(MockLLMProvider(), MockTTSProvider(), tmp_path).generate(source, minutes=3)
    data = json.loads((job/'usage/llm_usage.json').read_text())
    assert data['total']['request_count'] > 0
    assert data['total']['unknown_usage_count'] == data['total']['request_count']
    assert data['total']['estimated_cost'] is None
    assert 'prompt' not in json.dumps(data).lower()
    assert 'key' not in json.dumps(data).lower()


@pytest.mark.parametrize('broken', [False, 'length', 'malformed', 'schema'])
def test_wire_options_usage_redaction_and_permanent_output(monkeypatch, broken):
    monkeypatch.setenv('TEST_DEEPSEEK_KEY', 'sk-wire-only')
    calls = []
    provider = CompatibleLLMProvider(spec(api_key_env='TEST_DEEPSEEK_KEY', reasoning_policy='bookcast-v1',
        generation={'max_tokens': 2048})).for_task('synthesis/book/00-0000')
    content = '{"themes":[{"title":"主题","summary":"解释","claim_ids":["c1"],"importance":2}]}'
    response = {'choices': [{'message': {'content': '{}' if broken == 'schema' else content,
                                        'reasoning_content': 'private thinking'},
                             'finish_reason': 'length' if broken == 'length' else 'stop'}],
                'model': 'deepseek-v4.1-flash', 'usage': {'prompt_tokens': 12, 'completion_tokens': 7,
                    'prompt_cache_hit_tokens': 0, 'completion_tokens_details': {'reasoning_tokens': 3}}}
    class Transport:
        def open(self, req, timeout):
            calls.append(req)
            return io.BytesIO(b'broken' if broken == 'malformed' else json.dumps(response).encode())
    monkeypatch.setattr('bookcast.adapters.compatible.request.build_opener', lambda *a: Transport())
    chain, history = ProviderChain([provider]), []
    def invoke(p):
        return p.generate_structured('Return JSON', Synthesis)
    if broken:
        with pytest.raises(ProviderError) as exc:
            chain.execute(task='synthesis/book/00-0000', kind='llm', prompt_version='test', input_hash='h',
                          invoke=invoke, persist=lambda _: {'result.json':'a'*64}, observe=lambda a: history.append(a.model_copy(deep=True)))
        assert exc.value.kind == ErrorKind.SCHEMA
    else:
        chain.execute(task='synthesis/book/00-0000', kind='llm', prompt_version='test', input_hash='h',
                      invoke=invoke, persist=lambda _: {'result.json':'a'*64}, observe=lambda a: history.append(a.model_copy(deep=True)))
    sent = json.loads(calls[0].data)
    assert sent['thinking'] == {'type':'enabled'} and sent['reasoning_effort'] == 'high' and sent['max_tokens'] == 2048
    assert calls[0].get_header('Authorization') == 'Bearer sk-wire-only'
    attempt = history[-1]
    assert attempt.generation.options.reasoning_effort == 'high'
    assert attempt.provider_reported_usage is None if broken == 'malformed' else attempt.provider_reported_usage.output_tokens == 7
    raw = attempt.model_dump_json()
    assert 'sk-wire-only' not in raw and 'private thinking' not in raw
    assert attempt.reported_model == (None if broken == 'malformed' else 'deepseek-v4.1-flash')


def install_mock_wire(monkeypatch, fail_second=False):
    """Actual compatible transport path; content is deliberately generated by Mock."""
    calls = []
    models = {'analysis': EvidenceAnalysis, 'synthesis': Synthesis, 'dialogue': SegmentScript, 'consistency': ConsistencyReview}
    class Transport:
        def open(self, req, timeout):
            sent = json.loads(req.data)
            prompt = sent['messages'][-1]['content']
            data = json.loads(prompt)
            calls.append((req.full_url, data, sent))
            if fail_second and req.full_url.startswith('https://example.invalid') and data.get('chapter', {}).get('id') == '0002':
                raise HTTPError(req.full_url, 402, 'private', {}, io.BytesIO(b'private upstream body'))
            content = MockLLMProvider().generate_structured(prompt, models[data['operation']]).model_dump_json()
            return io.BytesIO(json.dumps({'choices':[{'message':{'content':content},'finish_reason':'stop'}],
                'model':'deepseek-flash', 'usage':{'prompt_tokens':11, 'completion_tokens':5}}).encode())
    monkeypatch.setattr('bookcast.adapters.compatible.request.build_opener', lambda *a: Transport())
    return calls


def test_deepseek_invalid_analysis_reports_field_and_retry_preserves_chapters(tmp_path, monkeypatch):
    source = Path(__file__).parents[1]/'examples/content-demo.txt'
    requests = []
    fail = [2]
    models = {'analysis': EvidenceAnalysis, 'synthesis': Synthesis, 'dialogue': SegmentScript,
              'consistency': ConsistencyReview}
    class Transport:
        def open(self, req, timeout):
            sent = json.loads(req.data)
            data = json.loads(sent['messages'][-1]['content'])
            requests.append(data)
            content = MockLLMProvider().generate_structured(sent['messages'][-1]['content'],
                                                              models[data['operation']]).model_dump()
            if data['operation'] == 'analysis' and data['chapter']['id'] == '0003' and fail[0] > 0:
                fail[0] -= 1
                content['core_ideas'] = None
            return io.BytesIO(json.dumps({'choices': [{'message': {'content': json.dumps(content)},
                'finish_reason': 'stop'}], 'model': 'deepseek-flash',
                'usage': {'prompt_tokens': 10, 'completion_tokens': 5}}).encode())
    monkeypatch.setattr('bookcast.adapters.compatible.request.build_opener', lambda *a: Transport())
    provider = CompatibleLLMProvider(ProviderSpec(name='deepseek', kind='llm', type='openai-compatible',
        model='deepseek-flash', base_url='https://api.deepseek.com'))
    pipeline = Pipeline(provider, MockTTSProvider(), tmp_path/'out')
    with pytest.raises(Exception, match='schema_error'):
        pipeline.generate(source)
    job = next((tmp_path/'out').iterdir())
    before = {p.name: sha256_file(p) for p in (job/'analysis').glob('000*.json')}
    failed = load_manifest(job/'manifest.json').ai_calls[-1]
    assert failed.task == 'analysis:0003:0001'
    raw_attempt = json.loads((job/'manifest.json').read_text())['ai_calls'][-1]
    assert not {'error_type', 'validation_field', 'validation_reason'} & raw_attempt.keys()
    events = [json.loads(line) for line in (job/'logs/events.jsonl').read_text().splitlines()]
    assert any(e['event'] == 'attempt' and e['error_type'] == 'ValidationError'
               and e['chapter'] == '0003' and e['model'] == 'deepseek-flash'
               and e['task_id'] == 'analysis:0003:0001' and e['schema_model'] == 'EvidenceAnalysis'
               and e['validation_field'] == 'core_ideas' and e['validation_reason'] == 'list_type'
               and e['finish_reason'] == 'stop'
               for e in events)
    count = len(requests)
    pipeline.resume_job(job, retry=True)
    assert all(sha256_file(job/'analysis'/name) == digest for name, digest in before.items())
    assert all(r.get('chapter', {}).get('id') not in {'0001', '0002'}
               for r in requests[count:] if r['operation'] == 'analysis')


def test_deepseek_schema_guidance_is_adapter_only(monkeypatch):
    sent = []
    class Transport:
        def open(self, req, timeout):
            sent.append(json.loads(req.data))
            return io.BytesIO(json.dumps({'choices': [{'message': {'content':
                '{"themes":[{"title":"题","summary":"说明","claim_ids":["c1"],"importance":2}]}'},
                'finish_reason': 'stop'}]}).encode())
    monkeypatch.setattr('bookcast.adapters.compatible.request.build_opener', lambda *a: Transport())
    for base_url in ('https://api.deepseek.com', 'https://api.openai.com/v1'):
        provider = CompatibleLLMProvider(ProviderSpec(name='remote', kind='llm', type='openai-compatible',
            model='test-model', base_url=base_url))
        assert provider.generate_structured('Return JSON', Synthesis).themes[0].title == '题'
    assert sent[0]['response_format'] == sent[1]['response_format'] == {'type': 'json_object'}
    assert 'Every listed property' in sent[0]['messages'][0]['content']
    assert 'Every listed property' not in sent[1]['messages'][0]['content']


@pytest.mark.parametrize('field', ['evidence', 'core_ideas'])
def test_real_failure_shape_gets_one_journaled_deepseek_correction(monkeypatch, field):
    """events.jsonl showed too_long at evidence, then core_ideas on chapter 0003."""
    sent = []
    finding = {'text': '简短转述', 'evidence_id': 'e0001'}
    valid = {'chapter_id': '0003', 'chunk_id': '0001',
             **{name: [] for name in ('arguments', 'evidence', 'examples', 'people', 'concepts',
                                       'counter_arguments', 'connections', 'key_passages')},
             'core_ideas': [finding], 'is_mock': False}

    class Transport:
        def open(self, req, timeout):
            sent.append(json.loads(req.data))
            result = dict(valid)
            if len(sent) == 1:
                result[field] = [finding] * 7  # actual failure class: at least seven, schema permits six
            return io.BytesIO(json.dumps({'choices': [{'message': {'content': json.dumps(result)},
                'finish_reason': 'stop'}], 'model': 'deepseek-flash',
                'usage': {'prompt_tokens': 8952, 'completion_tokens': 2627}}).encode())

    monkeypatch.setattr('bookcast.adapters.compatible.request.build_opener', lambda *a: Transport())
    provider = CompatibleLLMProvider(ProviderSpec(name='deepseek', kind='llm', type='openai-compatible',
        model='deepseek-flash', base_url='https://api.deepseek.com'))
    history = []
    result = ProviderChain([provider]).execute(task='analysis:0003:0001', kind='llm',
        prompt_version='content-analysis-v3', input_hash='h',
        invoke=lambda p: p.generate_structured('bounded chapter payload', EvidenceAnalysis),
        persist=lambda _: {'analysis/0003/0001.json': 'a'*64},
        observe=lambda a: history.append(a.model_copy(deep=True)))
    assert result and len(sent) == 2
    terminal = [a for a in history if a.status in {'failed_retryable', 'completed'}]
    assert [a.status for a in terminal] == ['failed_retryable', 'completed']
    assert len({a.id for a in terminal}) == 2
    assert terminal[0].validation_field == field and terminal[0].validation_reason == 'too_long'
    assert terminal[0].provider_reported_usage.input_tokens == 8952
    assert terminal[0].finish_reason == 'stop'
    assert f'{field} must contain at most 6 items' in sent[1]['messages'][0]['content']
    assert sent[0]['response_format'] == {'type': 'json_object'}
    assert 'at most 6 items' not in sent[0]['messages'][0]['content']
    assert not {'finish_reason', 'validation_field'} & terminal[0].model_dump().keys()


def test_real_failure_shape_secondary_array_overflow_is_repaired(monkeypatch):
    """Real failure pattern: call 1 failed on evidence too_long; retry call 2 fixed evidence but core_ideas had 7 items."""
    sent = []
    finding = {'text': '简短转述', 'evidence_id': 'e0001'}
    call1_payload = {'chapter_id': '0003', 'chunk_id': '0002',
                     **{name: [] for name in ('arguments', 'examples', 'people', 'concepts',
                                               'counter_arguments', 'connections', 'key_passages')},
                     'core_ideas': [finding], 'evidence': [finding] * 7, 'is_mock': False}
    call2_payload = {'chapter_id': '0003', 'chunk_id': '0002',
                     **{name: [] for name in ('arguments', 'examples', 'people', 'concepts',
                                               'counter_arguments', 'connections', 'key_passages')},
                     'core_ideas': [finding] * 7, 'evidence': [finding] * 6, 'is_mock': False}

    class Transport:
        def open(self, req, timeout):
            sent.append(json.loads(req.data))
            payload = call1_payload if len(sent) == 1 else call2_payload
            return io.BytesIO(json.dumps({'choices': [{'message': {'content': json.dumps(payload)},
                'finish_reason': 'stop'}], 'model': 'deepseek-flash',
                'usage': {'prompt_tokens': 6364, 'completion_tokens': 2370}}).encode())

    monkeypatch.setattr('bookcast.adapters.compatible.request.build_opener', lambda *a: Transport())
    provider = CompatibleLLMProvider(ProviderSpec(name='deepseek', kind='llm', type='openai-compatible',
        model='deepseek-flash', base_url='https://api.deepseek.com'))
    history = []
    result = ProviderChain([provider]).execute(task='analysis:0003:0002', kind='llm',
        prompt_version='content-analysis-v3', input_hash='h',
        invoke=lambda p: p.generate_structured('bounded chapter payload', EvidenceAnalysis),
        persist=lambda r: {'analysis/0003/0002.json': 'a'*64} if len(r.core_ideas) == 6 and len(r.evidence) == 6 else {},
        observe=lambda a: history.append(a.model_copy(deep=True)))
    assert result and len(sent) == 2
    terminal = [a for a in history if a.status in {'failed_retryable', 'completed'}]
    assert [a.status for a in terminal] == ['failed_retryable', 'completed']
    assert terminal[0].validation_field == 'evidence' and terminal[0].validation_reason == 'too_long'
    assert terminal[1].status == 'completed'


def test_2026_09_25_evidence_overflow_repeated_on_bounded_retry(monkeypatch):
    """Reconstructed from actual event field/reason; raw model body was not retained."""
    payload = json.loads((Path(__file__).parent/'fixtures/deepseek_analysis_0003_evidence_overflow.json').read_text())
    requests = []

    class Transport:
        def open(self, req, timeout):
            requests.append(json.loads(req.data))
            return io.BytesIO(json.dumps({'choices': [{'message': {'content': json.dumps(payload)},
                'finish_reason': 'stop'}], 'model': 'deepseek-flash',
                'usage': {'prompt_tokens': 6488, 'completion_tokens': 2198}}).encode())

    monkeypatch.setattr('bookcast.adapters.compatible.request.build_opener', lambda *a: Transport())
    provider = CompatibleLLMProvider(ProviderSpec(name='deepseek', kind='llm', type='openai-compatible',
        model='deepseek-flash', base_url='https://api.deepseek.com'))
    history = []
    validated = []
    result = ProviderChain([provider]).execute(task='analysis:0003:0001', kind='llm',
        prompt_version='content-analysis-v3', input_hash='h',
        invoke=lambda p: p.generate_structured('redacted chapter', EvidenceAnalysis),
        persist=lambda value: validated.append(value) or {},
        observe=lambda a: history.append(a.model_copy(deep=True)))
    assert len(requests) == 2
    assert result == {} and len(validated[0].evidence) == 6
    terminal = [a for a in history if a.status in {'failed_retryable', 'completed'}]
    assert [a.status for a in terminal] == ['failed_retryable', 'completed']
    assert (terminal[0].error_type, terminal[0].validation_field, terminal[0].validation_reason) == (
        'ValidationError', 'evidence', 'too_long')
    assert 'evidence must contain at most 6 items' in requests[1]['messages'][0]['content']


def test_deepseek_retry_still_rejects_invalid_evidence_item(monkeypatch):
    payload = json.loads((Path(__file__).parent/'fixtures/deepseek_analysis_0003_evidence_overflow.json').read_text())
    payload['evidence'] = payload['evidence'][:6]
    payload['evidence'][0]['evidence_id'] = 'invalid'
    count = 0

    class Transport:
        def open(self, req, timeout):
            nonlocal count
            count += 1
            return io.BytesIO(json.dumps({'choices': [{'message': {'content': json.dumps(payload)},
                'finish_reason': 'stop'}]}).encode())

    monkeypatch.setattr('bookcast.adapters.compatible.request.build_opener', lambda *a: Transport())
    provider = CompatibleLLMProvider(ProviderSpec(name='deepseek', kind='llm', type='openai-compatible',
        model='deepseek-flash', base_url='https://api.deepseek.com'))
    with pytest.raises(ProviderError) as failure:
        ProviderChain([provider]).execute(task='analysis:0003:0001', kind='llm',
            prompt_version='content-analysis-v3', input_hash='h',
            invoke=lambda p: p.generate_structured('redacted chapter', EvidenceAnalysis),
            persist=lambda _: {}, observe=lambda _: None)
    assert count == 1
    assert failure.value.kind == ErrorKind.SCHEMA
    assert failure.value.validation_field == 'evidence.0.evidence_id'


def test_required_field_overflow_on_retry_is_not_silently_clamped(monkeypatch):
    """If core_ideas (minItems=1) is the warned field, its overflow must NOT be
    silently truncated on the retry — the model must fix it.  If it doesn't,
    the call fails permanently after exactly 2 API calls."""
    finding = {'text': '简短转述', 'evidence_id': 'e0001'}
    # First call: core_ideas has 7 items → too_long → retry
    # Second call: model still returns 7 core_ideas → should NOT be clamped → permanent fail
    payload = {'chapter_id': '0003', 'chunk_id': '0001',
               'core_ideas': [finding] * 7,
               **{name: [] for name in ('arguments', 'evidence', 'examples', 'people', 'concepts',
                                         'counter_arguments', 'connections', 'key_passages')},
               'is_mock': False}
    sent = []

    class Transport:
        def open(self, req, timeout):
            sent.append(json.loads(req.data))
            return io.BytesIO(json.dumps({'choices': [{'message': {'content': json.dumps(payload)},
                'finish_reason': 'stop'}], 'model': 'deepseek-flash',
                'usage': {'prompt_tokens': 5000, 'completion_tokens': 1500}}).encode())

    monkeypatch.setattr('bookcast.adapters.compatible.request.build_opener', lambda *a: Transport())
    provider = CompatibleLLMProvider(ProviderSpec(name='deepseek', kind='llm', type='openai-compatible',
        model='deepseek-flash', base_url='https://api.deepseek.com'))
    history = []
    with pytest.raises(ProviderError) as exc_info:
        ProviderChain([provider]).execute(task='analysis:0003:0001', kind='llm',
            prompt_version='content-analysis-v3', input_hash='h',
            invoke=lambda p: p.generate_structured('payload', EvidenceAnalysis),
            persist=lambda _: {}, observe=lambda a: history.append(a.model_copy(deep=True)))
    assert exc_info.value.kind == ErrorKind.SCHEMA
    assert len(sent) == 2  # exactly one retry, then permanent failure
    terminal = [a for a in history if a.status in {'failed_retryable', 'failed_permanent'}]
    assert terminal[0].validation_field == 'core_ideas' and terminal[0].validation_reason == 'too_long'
    assert terminal[1].validation_field == 'core_ideas' and terminal[1].validation_reason == 'too_long'
    assert terminal[1].status == 'failed_permanent'


def test_unknown_validation_reason_is_not_retried(monkeypatch):
    """Validation reasons outside the known safe set must NOT trigger a retry."""
    finding = {'text': '简短转述', 'evidence_id': 'e0001'}
    payload = {'chapter_id': '0003', 'chunk_id': '0001',
               'core_ideas': [finding], 'is_mock': False,
               **{name: [] for name in ('arguments', 'evidence', 'examples', 'people', 'concepts',
                                         'counter_arguments', 'connections', 'key_passages')}}
    # Tamper: make evidence_id violate the pattern constraint → reason = 'string_pattern_mismatch'
    payload['core_ideas'][0]['evidence_id'] = 'bad_id_format'
    count = 0

    class Transport:
        def open(self, req, timeout):
            nonlocal count
            count += 1
            return io.BytesIO(json.dumps({'choices': [{'message': {'content': json.dumps(payload)},
                'finish_reason': 'stop'}]}).encode())

    monkeypatch.setattr('bookcast.adapters.compatible.request.build_opener', lambda *a: Transport())
    provider = CompatibleLLMProvider(ProviderSpec(name='deepseek', kind='llm', type='openai-compatible',
        model='deepseek-flash', base_url='https://api.deepseek.com'))
    with pytest.raises(ProviderError) as exc_info:
        ProviderChain([provider]).execute(task='analysis:0003:0001', kind='llm',
            prompt_version='content-analysis-v3', input_hash='h',
            invoke=lambda p: p.generate_structured('payload', EvidenceAnalysis),
            persist=lambda _: {}, observe=lambda _: None)
    assert count == 1  # no retry
    assert exc_info.value.kind == ErrorKind.SCHEMA


def test_deepseek_clamped_retry_still_runs_full_pydantic_validation(monkeypatch):
    """When retry clamps an overflowing evidence list to 6 items, full Pydantic validation
    must still execute on the clamped data. If any element has invalid fields, it must fail
    permanently with ErrorKind.SCHEMA and not be swallowed."""
    finding = {'text': '简短转述', 'evidence_id': 'e0001'}
    bad_finding = {'text': '简短转述', 'evidence_id': 'invalid_id'}
    call1_payload = {'chapter_id': '0003', 'chunk_id': '0001',
                     'core_ideas': [finding],
                     'evidence': [finding] * 7,
                     **{name: [] for name in ('arguments', 'examples', 'people', 'concepts',
                                               'counter_arguments', 'connections', 'key_passages')},
                     'is_mock': False}
    call2_payload = {'chapter_id': '0003', 'chunk_id': '0001',
                     'core_ideas': [finding],
                     'evidence': [bad_finding] + [finding] * 6,
                     **{name: [] for name in ('arguments', 'examples', 'people', 'concepts',
                                               'counter_arguments', 'connections', 'key_passages')},
                     'is_mock': False}
    sent = []

    class Transport:
        def open(self, req, timeout):
            sent.append(json.loads(req.data))
            payload = call1_payload if len(sent) == 1 else call2_payload
            return io.BytesIO(json.dumps({'choices': [{'message': {'content': json.dumps(payload)},
                'finish_reason': 'stop'}], 'model': 'deepseek-flash'}).encode())

    monkeypatch.setattr('bookcast.adapters.compatible.request.build_opener', lambda *a: Transport())
    provider = CompatibleLLMProvider(ProviderSpec(name='deepseek', kind='llm', type='openai-compatible',
        model='deepseek-flash', base_url='https://api.deepseek.com'))
    history = []
    with pytest.raises(ProviderError) as exc_info:
        ProviderChain([provider]).execute(task='analysis:0003:0001', kind='llm',
            prompt_version='content-analysis-v3', input_hash='h',
            invoke=lambda p: p.generate_structured('payload', EvidenceAnalysis),
            persist=lambda _: {}, observe=lambda a: history.append(a.model_copy(deep=True)))
    assert len(sent) == 2
    assert exc_info.value.kind == ErrorKind.SCHEMA
    assert exc_info.value.validation_field == 'evidence.0.evidence_id'
    terminal = [a for a in history if a.status in {'failed_retryable', 'failed_permanent'}]
    assert [a.status for a in terminal] == ['failed_retryable', 'failed_permanent']
    assert terminal[1].validation_field == 'evidence.0.evidence_id'


def test_2026_09_25_analysis_0015_concepts_overflow_and_conversational_retry(monkeypatch):
    """Real failure pattern on chapter 15:
    Call 1: concepts has 7 items (> maxItems 6) -> triggers schema retry for concepts.
    Call 2: DeepSeek returns corrected concepts wrapped in conversational preamble and markdown fence.
    Verifies that preamble and code fence are safely stripped, concepts validated, and task completes."""
    fixture_path = Path(__file__).parent / 'fixtures/deepseek_analysis_0015_concepts_overflow.json'
    call1_payload = json.loads(fixture_path.read_text())
    call2_payload = json.loads(fixture_path.read_text())
    call2_payload['concepts'] = call2_payload['concepts'][:6]
    call2_text = (
        "Here is the corrected JSON matching the schema with concepts limited to at most 6 items:\n"
        "```json\n"
        f"{json.dumps(call2_payload, ensure_ascii=False)}\n"
        "```\n"
        "Note: I have adjusted the concepts list to conform strictly to the limit."
    )
    sent = []

    class Transport:
        def open(self, req, timeout):
            sent.append(json.loads(req.data))
            content = json.dumps(call1_payload) if len(sent) == 1 else call2_text
            return io.BytesIO(json.dumps({'choices': [{'message': {'content': content},
                'finish_reason': 'stop'}], 'model': 'deepseek-flash',
                'usage': {'prompt_tokens': 4614, 'completion_tokens': 2019}}).encode())

    monkeypatch.setattr('bookcast.adapters.compatible.request.build_opener', lambda *a: Transport())
    provider = CompatibleLLMProvider(ProviderSpec(name='deepseek', kind='llm', type='openai-compatible',
        model='deepseek-flash', base_url='https://api.deepseek.com'))
    history = []
    validated = []
    result = ProviderChain([provider]).execute(task='analysis:0015:0001', kind='llm',
        prompt_version='content-analysis-v3', input_hash='h',
        invoke=lambda p: p.generate_structured('chapter 15 payload', EvidenceAnalysis),
        persist=lambda value: validated.append(value) or {'analysis/0015/0001.json': 'hash15'},
        observe=lambda a: history.append(a.model_copy(deep=True)))
    assert len(sent) == 2
    assert result == {'analysis/0015/0001.json': 'hash15'}
    assert len(validated[0].concepts) == 6
    terminal = [a for a in history if a.status in {'failed_retryable', 'completed'}]
    assert [a.status for a in terminal] == ['failed_retryable', 'completed']
    assert (terminal[0].error_type, terminal[0].validation_field, terminal[0].validation_reason) == (
        'ValidationError', 'concepts', 'too_long')
    assert 'concepts must contain at most 6 items' in sent[1]['messages'][0]['content']


def test_deepseek_json_with_raw_newlines_and_conversational_postamble(monkeypatch):
    """DeepSeek occasionally outputs raw newlines inside string values or adds post-brace commentary."""
    finding = {'text': '简短转述包含换行', 'evidence_id': 'e0001'}
    payload = {'chapter_id': '0015', 'chunk_id': '0001',
               'core_ideas': [finding], 'arguments': [], 'examples': [], 'people': [], 'concepts': [],
               'counter_arguments': [], 'connections': [], 'key_passages': [], 'evidence': [], 'is_mock': False}
    raw_json_with_newline = json.dumps(payload, ensure_ascii=False).replace('包含换行', '\n第二行')
    conversational_text = f"Certainly! Here is the JSON:\n{raw_json_with_newline}\nHope this helps!"

    class Transport:
        def open(self, req, timeout):
            return io.BytesIO(json.dumps({'choices': [{'message': {'content': conversational_text},
                'finish_reason': 'stop'}], 'model': 'deepseek-flash'}).encode())

    monkeypatch.setattr('bookcast.adapters.compatible.request.build_opener', lambda *a: Transport())
    provider = CompatibleLLMProvider(ProviderSpec(name='deepseek', kind='llm', type='openai-compatible',
        model='deepseek-flash', base_url='https://api.deepseek.com'))
    result = provider.generate_structured('prompt', EvidenceAnalysis)
    assert result.chapter_id == '0015'
    assert result.core_ideas[0].text == '简短转述\n第二行'


def test_real_failure_shape_deepseek_markdown_fence_is_safely_stripped(monkeypatch):
    """Real failure pattern on chapter 22: DeepSeek wrapped valid JSON in ```json ... ``` markdown fence."""
    finding = {'text': '简短转述', 'evidence_id': 'e0001'}
    payload = {'chapter_id': '0022', 'chunk_id': '0001',
               'core_ideas': [finding], 'arguments': [], 'examples': [], 'people': [], 'concepts': [],
               'counter_arguments': [], 'connections': [], 'key_passages': [], 'evidence': [], 'is_mock': False}
    wrapped_json = f"```json\n{json.dumps(payload, ensure_ascii=False)}\n```"

    class Transport:
        def open(self, req, timeout):
            return io.BytesIO(json.dumps({'choices': [{'message': {'content': wrapped_json},
                'finish_reason': 'stop'}], 'model': 'deepseek-flash',
                'usage': {'prompt_tokens': 6802, 'completion_tokens': 2029}}).encode())

    monkeypatch.setattr('bookcast.adapters.compatible.request.build_opener', lambda *a: Transport())

    # 1. DeepSeek provider safely strips markdown fence and validates
    ds_provider = CompatibleLLMProvider(ProviderSpec(name='deepseek', kind='llm', type='openai-compatible',
        model='deepseek-flash', base_url='https://api.deepseek.com'))
    result = ds_provider.generate_structured('bounded chapter payload', EvidenceAnalysis)
    assert result.chapter_id == '0022'
    assert result.chunk_id == '0001'
    assert len(result.core_ideas) == 1

    # 2. OpenAI provider leaves text intact and raises ValidationError on markdown fence
    openai_provider = CompatibleLLMProvider(ProviderSpec(name='openai', kind='llm', type='openai-compatible',
        model='gpt-4o', base_url='https://api.openai.com/v1'))
    with pytest.raises(ProviderError) as exc_info:
        openai_provider.generate_structured('bounded chapter payload', EvidenceAnalysis)
    assert exc_info.value.kind == ErrorKind.SCHEMA
    assert exc_info.value.validation_reason == 'json_invalid'
    assert exc_info.value.validation_field == '$'


def test_deepseek_array_correction_is_bounded_and_openai_does_not_correct(monkeypatch):
    finding = {'text': '简短转述', 'evidence_id': 'e0001'}
    invalid = {'chapter_id': '0003', 'chunk_id': '0001', 'core_ideas': [finding] * 7,
               **{name: [] for name in ('arguments', 'evidence', 'examples', 'people', 'concepts',
                                         'counter_arguments', 'connections', 'key_passages')}}
    sent = []

    class Transport:
        def open(self, req, timeout):
            sent.append(json.loads(req.data))
            return io.BytesIO(json.dumps({'choices': [{'message': {'content': json.dumps(invalid)},
                                                       'finish_reason': 'stop'}]}).encode())

    monkeypatch.setattr('bookcast.adapters.compatible.request.build_opener', lambda *a: Transport())
    for url, expected_calls in [('https://api.deepseek.com', 2), ('https://api.openai.com/v1', 1)]:
        sent.clear()
        provider = CompatibleLLMProvider(ProviderSpec(name='remote', kind='llm', type='openai-compatible',
            model='test-model', base_url=url))
        with pytest.raises(ProviderError) as error:
            ProviderChain([provider]).execute(task='analysis:0003:0001', kind='llm',
                prompt_version='test', input_hash='h',
                invoke=lambda p: p.generate_structured('payload', EvidenceAnalysis),
                persist=lambda _: {}, observe=lambda _: None)
        assert error.value.kind == ErrorKind.SCHEMA and len(sent) == expected_calls


def test_completed_calls_resume_and_only_effective_changes_invalidate(tmp_path, monkeypatch):
    calls = install_mock_wire(monkeypatch)
    source = Path(__file__).parents[1]/'examples/content-demo.txt'
    original = CompatibleLLMProvider(spec(reasoning_policy='bookcast-v1'))
    job = Pipeline(original, MockTTSProvider(), tmp_path/'out').generate(source)
    before = {p:(p.stat().st_mtime_ns,sha256_file(p)) for p in job.rglob('*')
              if p.is_file() and p.name != '.lock' and 'usage' not in p.parts}
    count = len(calls)
    Pipeline(CompatibleLLMProvider(spec(reasoning_policy='bookcast-v1')), MockTTSProvider(), tmp_path/'out').resume_job(job)
    assert len(calls) == count
    assert all((p.stat().st_mtime_ns,sha256_file(p)) == v for p,v in before.items())
    changed = CompatibleLLMProvider(spec(reasoning_policy='bookcast-v1', generation={'thinking':'disabled'}))
    Pipeline(changed, MockTTSProvider(), tmp_path/'out').resume_job(job)
    assert len(calls) > count and all(c[1]['operation'] != 'analysis' for c in calls[count:])
    for p,v in before.items():
        if 'chunks' in p.parts or 'audio' in p.parts:
            assert (p.stat().st_mtime_ns,sha256_file(p)) == v
    assert all(c.provider_reported_usage.input_tokens == 11 for c in load_manifest(job/'manifest.json').ai_calls if c.kind == 'llm')


def test_real_adapter_402_failover_does_not_repeat_completed_chapter(tmp_path, monkeypatch):
    calls = install_mock_wire(monkeypatch, fail_second=True)
    first = CompatibleLLMProvider(spec(reasoning_policy='bookcast-v1'))
    second_spec = spec(reasoning_policy='bookcast-v1').model_copy(update={'name':'B','base_url':'https://backup.invalid'})
    chain = ProviderChain([first, CompatibleLLMProvider(second_spec)])
    job = Pipeline(chain, MockTTSProvider(), tmp_path/'out').generate(Path(__file__).parents[1]/'examples/content-demo.txt')
    m = load_manifest(job/'manifest.json')
    assert any(c.error == 'quota_exhausted' and c.provider_reported_usage is None for c in m.ai_calls)
    extraction = [(url, d['chapter']['id']) for url,d,_ in calls if d['operation']=='analysis']
    assert [cid for _,cid in extraction] == ['0001','0002','0002','0003']
    assert extraction[2][0].startswith('https://backup.invalid')
    count = len(calls)
    Pipeline(chain, MockTTSProvider(), tmp_path/'out').resume_job(job)
    assert len(calls) == count


def test_interruption_after_durable_attempt_uses_task_configuration_on_resume(tmp_path, monkeypatch):
    from bookcast.pipeline import _Runner
    calls = install_mock_wire(monkeypatch)
    original = _Runner.observe
    def interrupt(runner, attempt):
        original(runner, attempt)
        if attempt.task == 'analysis:0001:0001' and attempt.status == 'completed':
            raise KeyboardInterrupt
    monkeypatch.setattr(_Runner, 'observe', interrupt)
    def pipe():
        return Pipeline(CompatibleLLMProvider(spec(reasoning_policy='bookcast-v1')), MockTTSProvider(), tmp_path/'out')
    with pytest.raises(KeyboardInterrupt):
        pipe().generate(Path(__file__).parents[1]/'examples/content-demo.txt')
    job = next((tmp_path/'out').iterdir())
    checkpoint = job/'analysis/chunks/0001-0001.json'
    before = (checkpoint.stat().st_mtime_ns,sha256_file(checkpoint))
    monkeypatch.setattr(_Runner, 'observe', original)
    pipe().resume_job(job)
    assert [d['chapter']['id'] for _,d,_ in calls if d['operation']=='analysis'] == ['0001','0002','0003']
    assert (checkpoint.stat().st_mtime_ns,sha256_file(checkpoint)) == before


def test_metadata_resets_on_next_direct_call(monkeypatch):
    provider = CompatibleLLMProvider(spec())
    responses = iter([b'{"choices":[{"message":{"content":"ok"}}],"model":"deepseek-flash","usage":{"prompt_tokens":5}}',
                      b'{"choices":[{"message":{"content":"ok"}}]}'])
    monkeypatch.setattr(provider,'_request',lambda *args:next(responses))
    provider.generate('first')
    assert provider.last_usage.input_tokens == 5
    provider.generate('second')
    assert provider.last_usage is None and provider.reported_model is None


def test_example_config_and_cli_do_not_read_or_print_keys(monkeypatch, tmp_path):
    path = Path(__file__).parents[1]/'examples/deepseek-kokoro.toml'
    settings = load_config(path)
    assert settings.llm_priority == ['deepseek'] and all(p.type != 'mock' for p in settings.providers)
    monkeypatch.setenv('DEEPSEEK_API_KEY','sk-do-not-print')
    result = CliRunner().invoke(app,['config','providers','--config',str(path)])
    assert result.exit_code == 0 and 'sk-do-not-print' not in result.output
    invalid = tmp_path/'bad.toml'
    invalid.write_text(path.read_text().replace('max_tokens = 16384','max_tokens = "sk-do-not-print"'))
    result = CliRunner().invoke(app,['config','providers','--config',str(invalid)])
    assert result.exit_code == 1 and 'sk-do-not-print' not in result.output


def test_real_failure_shape_chapter42_model_type_is_repaired(monkeypatch):
    """Real failure pattern on chapter 42/43: DeepSeek returned string elements for core_ideas (model_type error)."""
    sent = []
    finding = {'text': '奔月〔１〕', 'evidence_id': 'e0001'}
    call1_payload = {
        'chapter_id': '0043', 'chunk_id': '0001',
        'core_ideas': ['奔月〔１〕'],  # string element instead of EvidenceFinding dict
        **{name: [] for name in ('arguments', 'examples', 'people', 'concepts',
                                 'counter_arguments', 'connections', 'key_passages', 'evidence')},
        'is_mock': False
    }
    call2_payload = {
        'chapter_id': '0043', 'chunk_id': '0001',
        'core_ideas': [finding],
        **{name: [] for name in ('arguments', 'examples', 'people', 'concepts',
                                 'counter_arguments', 'connections', 'key_passages', 'evidence')},
        'is_mock': False
    }

    class Transport:
        def open(self, req, timeout):
            sent.append(json.loads(req.data))
            payload = call1_payload if len(sent) == 1 else call2_payload
            return io.BytesIO(json.dumps({'choices': [{'message': {'content': json.dumps(payload)},
                'finish_reason': 'stop'}], 'model': 'deepseek-flash',
                'usage': {'prompt_tokens': 953, 'completion_tokens': 148}}).encode())

    monkeypatch.setattr('bookcast.adapters.compatible.request.build_opener', lambda *a: Transport())
    provider = CompatibleLLMProvider(ProviderSpec(name='deepseek', kind='llm', type='openai-compatible',
        model='deepseek-flash', base_url='https://api.deepseek.com'))
    history = []
    result = ProviderChain([provider]).execute(task='analysis:0043:0001', kind='llm',
        prompt_version='content-analysis-v3', input_hash='h',
        invoke=lambda p: p.generate_structured('bounded chapter payload', EvidenceAnalysis),
        persist=lambda r: {'analysis/0043/0001.json': 'a'*64} if r.core_ideas[0].text == '奔月〔１〕' else {},
        observe=lambda a: history.append(a.model_copy(deep=True)))
    assert result and len(sent) == 2
    terminal = [a for a in history if a.status in {'failed_retryable', 'completed'}]
    assert [a.status for a in terminal] == ['failed_retryable', 'completed']
    assert terminal[0].validation_field == 'core_ideas.0' and terminal[0].validation_reason == 'model_type'
    assert "Property 'core_ideas.0' must be an object" in sent[1]['messages'][0]['content']
    assert terminal[1].status == 'completed'


def test_multi_field_schema_drift_with_null_and_repair(monkeypatch):
    """Multi-field schema drift: null arrays normalized locally, model_type in core_ideas repaired on retry."""
    sent = []
    finding = {'text': '奔月〔１〕', 'evidence_id': 'e0001'}
    call1_payload = {
        'chapter_id': '0043', 'chunk_id': '0001',
        'core_ideas': ['奔月〔１〕'],
        'arguments': None,  # null instead of []
        'counter_arguments': None,  # null instead of []
        **{name: [] for name in ('examples', 'people', 'concepts', 'connections', 'key_passages', 'evidence')},
        'is_mock': False
    }
    call2_payload = {
        'chapter_id': '0043', 'chunk_id': '0001',
        'core_ideas': [finding],
        **{name: [] for name in ('arguments', 'examples', 'people', 'concepts',
                                 'counter_arguments', 'connections', 'key_passages', 'evidence')},
        'is_mock': False
    }

    class Transport:
        def open(self, req, timeout):
            sent.append(json.loads(req.data))
            payload = call1_payload if len(sent) == 1 else call2_payload
            return io.BytesIO(json.dumps({'choices': [{'message': {'content': json.dumps(payload)},
                'finish_reason': 'stop'}], 'model': 'deepseek-flash',
                'usage': {'prompt_tokens': 953, 'completion_tokens': 148}}).encode())

    monkeypatch.setattr('bookcast.adapters.compatible.request.build_opener', lambda *a: Transport())
    provider = CompatibleLLMProvider(ProviderSpec(name='deepseek', kind='llm', type='openai-compatible',
        model='deepseek-flash', base_url='https://api.deepseek.com'))
    history = []
    result = ProviderChain([provider]).execute(task='analysis:0043:0001', kind='llm',
        prompt_version='content-analysis-v3', input_hash='h',
        invoke=lambda p: p.generate_structured('bounded chapter payload', EvidenceAnalysis),
        persist=lambda r: {'analysis/0043/0001.json': 'a'*64},
        observe=lambda a: history.append(a.model_copy(deep=True)))
    assert result and len(sent) == 2
    terminal = [a for a in history if a.status in {'failed_retryable', 'completed'}]
    assert [a.status for a in terminal] == ['failed_retryable', 'completed']


def test_repair_failure_is_permanent_and_no_infinite_retry(monkeypatch):
    """If repair call still fails validation, it must permanently fail without infinite retry (max 2 calls)."""
    sent = []
    invalid_payload = {
        'chapter_id': '0043', 'chunk_id': '0001',
        'core_ideas': ['仍为字符串'],
        **{name: [] for name in ('arguments', 'examples', 'people', 'concepts',
                                 'counter_arguments', 'connections', 'key_passages', 'evidence')},
        'is_mock': False
    }

    class Transport:
        def open(self, req, timeout):
            sent.append(json.loads(req.data))
            return io.BytesIO(json.dumps({'choices': [{'message': {'content': json.dumps(invalid_payload)},
                'finish_reason': 'stop'}], 'model': 'deepseek-flash',
                'usage': {'prompt_tokens': 953, 'completion_tokens': 148}}).encode())

    monkeypatch.setattr('bookcast.adapters.compatible.request.build_opener', lambda *a: Transport())
    provider = CompatibleLLMProvider(ProviderSpec(name='deepseek', kind='llm', type='openai-compatible',
        model='deepseek-flash', base_url='https://api.deepseek.com'))
    history = []
    with pytest.raises(ProviderError) as exc_info:
        ProviderChain([provider]).execute(task='analysis:0043:0001', kind='llm',
            prompt_version='content-analysis-v3', input_hash='h',
            invoke=lambda p: p.generate_structured('bounded chapter payload', EvidenceAnalysis),
            persist=lambda _: {},
            observe=lambda a: history.append(a.model_copy(deep=True)))
    assert exc_info.value.kind == ErrorKind.SCHEMA
    assert len(sent) == 2  # exactly 2 calls: 1 initial + 1 correction, no infinite retry
    terminal = [a for a in history if a.status in {'failed_retryable', 'failed_permanent'}]
    assert [a.status for a in terminal] == ['failed_retryable', 'failed_permanent']


def test_openai_provider_unaffected_by_schema_drift_retry(monkeypatch):
    """OpenAI provider does not use DeepSeek-specific correction or fence strip, fails immediately on schema error."""
    sent = []
    invalid_payload = {
        'chapter_id': '0043', 'chunk_id': '0001',
        'core_ideas': ['字符串观点'],
        **{name: [] for name in ('arguments', 'examples', 'people', 'concepts',
                                 'counter_arguments', 'connections', 'key_passages', 'evidence')},
        'is_mock': False
    }

    class Transport:
        def open(self, req, timeout):
            sent.append(json.loads(req.data))
            return io.BytesIO(json.dumps({'choices': [{'message': {'content': json.dumps(invalid_payload)},
                'finish_reason': 'stop'}], 'model': 'gpt-4o',
                'usage': {'prompt_tokens': 953, 'completion_tokens': 148}}).encode())

    monkeypatch.setattr('bookcast.adapters.compatible.request.build_opener', lambda *a: Transport())
    openai_provider = CompatibleLLMProvider(ProviderSpec(name='openai', kind='llm', type='openai-compatible',
        model='gpt-4o', base_url='https://api.openai.com/v1'))
    history = []
    with pytest.raises(ProviderError) as exc_info:
        ProviderChain([openai_provider]).execute(task='analysis:0043:0001', kind='llm',
            prompt_version='content-analysis-v3', input_hash='h',
            invoke=lambda p: p.generate_structured('bounded chapter payload', EvidenceAnalysis),
            persist=lambda _: {},
            observe=lambda a: history.append(a.model_copy(deep=True)))
    assert exc_info.value.kind == ErrorKind.SCHEMA
    assert len(sent) == 1  # exactly 1 call: OpenAI does not trigger DeepSeek schema correction
    terminal = [a for a in history if a.status == 'failed_permanent']
    assert len(terminal) == 1

def test_consistency_reasoning_effort_low(monkeypatch):
    monkeypatch.setenv('TEST_DEEPSEEK_KEY', 'sk-wire-only')
    calls = []
    
    # 1. Test consistency stage
    provider = CompatibleLLMProvider(spec(api_key_env='TEST_DEEPSEEK_KEY', reasoning_policy='bookcast-v1')).for_task('consistency:0001')
    response = {'choices': [{'message': {'content': '{}', 'reasoning_content': 'thinking'}, 'finish_reason': 'stop'}], 'model': 'deepseek', 'usage': {}}
    class Transport:
        def open(self, req, timeout):
            calls.append(req)
            return io.BytesIO(json.dumps(response).encode())
    monkeypatch.setattr('bookcast.adapters.compatible.request.build_opener', lambda *a: Transport())
    provider._chat("test")
    
    sent = json.loads(calls[0].data)
    assert sent.get('thinking') == {'type': 'enabled'}
    assert sent.get('reasoning_effort') == 'low'
    
    # 2. Test another stage (e.g., book_synthesis) to ensure they are not affected
    calls.clear()
    provider_synth = CompatibleLLMProvider(spec(api_key_env='TEST_DEEPSEEK_KEY', reasoning_policy='bookcast-v1')).for_task('synthesis/book/00')
    provider_synth._chat("test")
    
    sent_synth = json.loads(calls[0].data)
    assert sent_synth.get('thinking') == {'type': 'enabled'}
    assert sent_synth.get('reasoning_effort') == 'high'

    # 3. Test extraction stage
    calls.clear()
    provider_ext = CompatibleLLMProvider(spec(api_key_env='TEST_DEEPSEEK_KEY', reasoning_policy='bookcast-v1')).for_task('analysis:0001')
    provider_ext._chat("test")
    
    sent_ext = json.loads(calls[0].data)
    assert sent_ext.get('thinking') == {'type': 'disabled'}
    assert sent_ext.get('reasoning_effort') is None

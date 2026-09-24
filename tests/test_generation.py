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
                'synthesis/chapters/0001/00-0000': ('chapter_synthesis', 'enabled', 'low'),
                'synthesis/book/00-0000': ('book_synthesis', 'enabled', 'high'),
                'script:0001': ('dialogue', 'enabled', 'low'),
                'consistency:0001': ('consistency', 'enabled', 'low')}
    for task, (kind, thinking, effort) in expected.items():
        audit = resolve_generation(task, 'bookcast-v1', None)
        assert (audit.task_type, audit.options.thinking, audit.options.reasoning_effort) == (kind, thinking, effort)
        disabled = resolve_generation(task, 'bookcast-v1', GenerationConfig(thinking='disabled'))
        assert disabled.options.request_fields() == {'thinking': {'type': 'disabled'}}
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
    fail = [True]
    models = {'analysis': EvidenceAnalysis, 'synthesis': Synthesis, 'dialogue': SegmentScript,
              'consistency': ConsistencyReview}
    class Transport:
        def open(self, req, timeout):
            sent = json.loads(req.data)
            data = json.loads(sent['messages'][-1]['content'])
            requests.append(data)
            content = MockLLMProvider().generate_structured(sent['messages'][-1]['content'],
                                                              models[data['operation']]).model_dump()
            if data['operation'] == 'analysis' and data['chapter']['id'] == '0003' and fail[0]:
                fail[0] = False
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
    before = {p:(p.stat().st_mtime_ns,sha256_file(p)) for p in job.rglob('*') if p.is_file() and p.name != '.lock'}
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

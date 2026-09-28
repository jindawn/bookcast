"""Offline replay of the observed Qwen people/too_long validation signature."""

import json
import io
from pathlib import Path

import pytest

from bookcast.adapters.compatible import CompatibleLLMProvider
from bookcast.adapters.qwen_llm import QwenLLMProvider
from bookcast.content_models import EvidenceAnalysis
from bookcast.generation import ProviderUsage
from bookcast.llm_usage import usage_snapshot
from bookcast.model_routing import RoutedLLMChain
from bookcast.provider_api import ErrorKind, ProviderError
from bookcast.provider_api import ProviderRequestContext
from bookcast.provider_config import LLMRouting

from test_qwen_llm import qwen_spec, deepseek_spec


FIXTURE = Path(__file__).parent / 'fixtures/qwen_people_too_long_reconstructed.json'


def _case():
    record = json.loads(FIXTURE.read_text())
    assert record['provenance'].startswith('reconstructed_')
    invalid = record['response']
    valid = {**invalid, 'people': invalid['people'][:6]}
    return invalid, valid


def _run(monkeypatch, qwen_responses, deepseek_responses, *, task='analysis:0003:0005'):
    sent, calls = [], []

    def reply(provider_name, responses):
        queue = iter(responses)
        def request(self, route, payload):
            sent.append((provider_name, task, payload))
            value = next(queue)
            if isinstance(value, Exception):
                raise value
            self.last_usage = ProviderUsage(input_tokens=10, output_tokens=5, cache_hit_tokens=0)
            return json.dumps({'choices': [{'message': {'content': json.dumps(value)}, 'finish_reason': 'stop'}],
                               'model': self.model, 'usage': {'prompt_tokens': 10, 'completion_tokens': 5}}).encode()
        return request

    monkeypatch.setattr(QwenLLMProvider, '_request', reply('qwen', qwen_responses))
    monkeypatch.setattr(CompatibleLLMProvider, '_request', reply('deepseek', deepseek_responses))
    chain = RoutedLLMChain([QwenLLMProvider(qwen_spec()), CompatibleLLMProvider(deepseek_spec())],
                           LLMRouting(general=['qwen'], cheap=['qwen'], complex=['deepseek'],
                                      high_quality=['deepseek']))
    def invoke(provider):
        return provider.generate_structured('frozen prompt', EvidenceAnalysis)
    def persist(result):
        assert isinstance(result, EvidenceAnalysis)
        return {'analysis.json': 'hash'}
    kwargs = dict(task=task, kind='llm', prompt_version='frozen', input_hash='frozen-hash',
                  invoke=invoke, persist=persist, observe=lambda call: calls.append(call.model_copy(deep=True)))
    return chain, kwargs, sent, calls


def _terminal(calls):
    return [call for call in calls if call.status in {'completed', 'failed_retryable', 'failed_permanent'}]


def test_first_qwen_response_passes_without_repair_or_fallback(monkeypatch):
    _, valid = _case()
    chain, kwargs, sent, calls = _run(monkeypatch, [valid], [])
    assert chain.execute(**kwargs) == {'analysis.json': 'hash'}
    assert [name for name, _, _ in sent] == ['qwen']
    assert [call.status for call in _terminal(calls)] == ['completed']


def test_qwen_repairs_once_using_same_strict_schema(monkeypatch):
    invalid, valid = _case()
    chain, kwargs, sent, calls = _run(monkeypatch, [invalid, valid], [])
    chain.execute(**kwargs)
    assert [name for name, _, _ in sent] == ['qwen', 'qwen']
    assert sent[1][2]['response_format'] == {'type': 'json_object'}
    assert 'Maximum 6 items' in sent[1][2]['messages'][0]['content']
    assert [call.status for call in _terminal(calls)] == ['failed_retryable', 'completed']


def test_qwen_two_failures_fallback_once_and_bill_each_attempt_once(monkeypatch):
    invalid, valid = _case()
    chain, kwargs, sent, calls = _run(monkeypatch, [invalid, invalid], [valid])
    chain.execute(**kwargs)
    assert [name for name, _, _ in sent] == ['qwen', 'qwen', 'deepseek']
    final = _terminal(calls)
    assert [call.status for call in final] == ['failed_retryable', 'failed_permanent', 'completed']
    assert [call.provider for call in final] == ['qwen', 'qwen', 'deepseek']
    snapshot = usage_snapshot(final)
    assert snapshot['total']['request_count'] == 3
    assert snapshot['total']['input_tokens'] == 30
    assert snapshot['total']['output_tokens'] == 15


def test_both_providers_schema_fail_is_permanent_and_bounded(monkeypatch):
    invalid, _ = _case()
    chain, kwargs, sent, calls = _run(monkeypatch, [invalid, invalid], [invalid])
    with pytest.raises(ProviderError) as exc:
        chain.execute(**kwargs)
    assert exc.value.kind == ErrorKind.SCHEMA
    assert [name for name, _, _ in sent] == ['qwen', 'qwen', 'deepseek']
    assert _terminal(calls)[-1].status == 'failed_permanent'


def test_authentication_error_is_not_repaired_or_escalated(monkeypatch):
    _, valid = _case()
    chain, kwargs, sent, _ = _run(monkeypatch, [ProviderError(ErrorKind.AUTH)], [valid])
    with pytest.raises(ProviderError) as exc:
        chain.execute(**kwargs)
    assert exc.value.kind == ErrorKind.AUTH
    assert [name for name, _, _ in sent] == ['qwen']


def test_fallback_does_not_change_next_task_route(monkeypatch):
    invalid, valid = _case()
    chain, kwargs, sent, _ = _run(monkeypatch, [invalid, invalid, valid], [valid])
    chain.execute(**kwargs)
    chain.execute(**{**kwargs, 'task': 'analysis:0003:0006'})
    assert [name for name, _, _ in sent] == ['qwen', 'qwen', 'deepseek', 'qwen']


def test_physical_telemetry_records_each_repair_and_fallback(monkeypatch, tmp_path):
    invalid, valid = _case()
    responses = iter([invalid, invalid, valid])
    class Opener:
        def open(self, request, timeout):
            value = next(responses)
            body = {'choices': [{'message': {'content': json.dumps(value)}, 'finish_reason': 'stop'}],
                    'usage': {'prompt_tokens': 10, 'completion_tokens': 5,
                              'prompt_tokens_details': {'cached_tokens': 0}}}
            return io.BytesIO(json.dumps(body).encode())
    monkeypatch.setattr('bookcast.adapters.compatible.request.build_opener', lambda *args: Opener())
    qwen = QwenLLMProvider(qwen_spec())
    deepseek = CompatibleLLMProvider(deepseek_spec())
    chain = RoutedLLMChain([qwen, deepseek], LLMRouting(general=['qwen'], cheap=['qwen'],
                           complex=['deepseek'], high_quality=['deepseek']))
    calls = []
    path = tmp_path / 'physical_requests.jsonl'
    chain.execute(task='analysis:0003:0005', kind='llm', prompt_version='frozen', input_hash='hash',
                  invoke=lambda provider: provider.generate_structured('frozen prompt', EvidenceAnalysis),
                  persist=lambda _: {'analysis.json': 'hash'},
                  observe=lambda call: calls.append(call.model_copy(deep=True)),
                  context=ProviderRequestContext(job_id='offline', output_id='offline',
                      logical_chunk_id='analysis:0003:0005', provider='', model='', telemetry_path=path))
    physical = [json.loads(line) for line in path.read_text().splitlines()]
    assert [(row['provider'], row['physical_attempt_index'], row['http_status']) for row in physical] == [
        ('qwen', 0, 200), ('qwen', 1, 200), ('deepseek', 0, 200)]
    assert usage_snapshot(_terminal(calls))['total']['request_count'] == len(physical) == 3

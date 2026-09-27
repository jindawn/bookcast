"""No paid calls: frozen identity, experiment guardrails and quality metrics."""
import json
import io
from pathlib import Path
from urllib import error as urlerr

import pytest

from bookcast.adapters.compatible import CompatibleLLMProvider, _deepseek_error_observation
from bookcast.content_models import SegmentScript
from bookcast.generation import GenerationConfig
from bookcast.provider_config import ProviderSpec
from bookcast.storage import fingerprint
from scripts.llm_reasoning_dialogue import (
    FIXTURE, baseline_report, candidate_prompt, candidate_totals, fixture, form_metrics,
    quality_gate, run_live,
)
from scripts.llm_reasoning_diagnostic import run as run_diagnostic


def test_deepseek_http_error_observation_is_bounded_and_redacted(tmp_path, monkeypatch):
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'diagnostic-secret-123456')
    prompt = 'this is a private user prompt that must never enter diagnostics'
    body = json.dumps({'error': {'code': 'invalid_request_error',
                                 'message': 'Bad schema; Bearer diagnostic-secret-123456 token=diagnostic-secret-123456',
                                 'request_id': 'body-id'}}).encode()

    class Transport:
        def open(self, req, timeout):
            raise urlerr.HTTPError(req.full_url, 400, 'Bad Request',
                                   {'Content-Type': 'application/json', 'X-Request-Id': 'request-id-1',
                                    'Authorization': 'diagnostic-secret-123456'}, io.BytesIO(body))

    monkeypatch.setattr('bookcast.adapters.compatible.request.build_opener', lambda *a: Transport())
    spec = ProviderSpec(name='deepseek', kind='llm', type='openai-compatible', model='deepseek-flash',
                        base_url='https://api.deepseek.com', api_key_env='DEEPSEEK_API_KEY')
    provider = CompatibleLLMProvider(spec)
    from bookcast.provider_api import ProviderRequestContext, ProviderError, ErrorKind
    provider.set_context(ProviderRequestContext(job_id=None, output_id=None, logical_chunk_id='test',
                                                provider='deepseek', model='deepseek-flash',
                                                telemetry_path=tmp_path / 'physical.jsonl'))
    with pytest.raises(ProviderError) as caught:
        provider._chat(prompt, SegmentScript.model_json_schema())
    assert caught.value.kind == ErrorKind.INPUT
    observation = provider.last_http_error_observation
    assert observation['http_status'] == 400
    assert observation['upstream_code'] == 'invalid_request_error'
    assert observation['request_id'] == 'request-id-1'
    assert observation['content_type'] == 'application/json'
    assert len(observation['body_summary']) <= 2048
    persisted = (tmp_path / 'physical.jsonl').read_text()
    assert 'diagnostic-secret-123456' not in persisted + json.dumps(observation)
    assert prompt not in persisted + json.dumps(observation)
    assert 'Authorization' not in persisted
    telemetry = json.loads(persisted)
    assert telemetry['error_kind'] == 'input_error'
    assert 'body_summary' not in telemetry
    assert telemetry['upstream_code'] == 'invalid_request_error'


def test_diagnostic_is_single_request_and_never_reuses_receipt(tmp_path, monkeypatch):
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'offline-test-only')
    calls = []

    def fail_once(self, prompt, schema):
        calls.append((prompt, schema))
        from bookcast.provider_api import ProviderError, ErrorKind
        self.last_http_error_observation = {'http_status': 400, 'upstream_code': 'model_not_found',
                                            'upstream_message': 'unknown model'}
        raise ProviderError(ErrorKind.INPUT)

    monkeypatch.setattr(CompatibleLLMProvider, '_chat', fail_once)
    row = run_diagnostic('baseline', tmp_path)
    assert row['status'] == 'http_error' and len(calls) == 1
    assert row['input_hash'] == fixture()['dialogue'][0]['baseline_input_hash']
    with pytest.raises(RuntimeError, match='already exists'):
        run_diagnostic('baseline', tmp_path)
    assert len(calls) == 1


def test_upstream_echoed_prompt_is_omitted_from_diagnostics():
    prompt = 'private content before the sensitive middle passage and after it'
    body = json.dumps({'error': {'code': 'bad_prompt',
                                 'message': 'Rejected: ' + prompt[12:52]}}).encode()
    observed = _deepseek_error_observation(400, body, {}, {'messages': [{'role': 'user', 'content': prompt}]})
    assert observed['upstream_message'] == '[upstream text omitted: echoed request]'
    assert prompt[12:52] not in json.dumps(observed)


def test_frozen_prompts_match_existing_manifest_without_rebuilding_pipeline():
    frozen = fixture()
    assert frozen['source_sha256'] == 'b30a16bd39cf8924da501dfa005be4bc9eea549045f08dd597669bde152dde94'
    assert len(frozen['dialogue']) == 2
    for item in frozen['dialogue']:
        assert set(item['payload']) == {'mode', 'segment', 'claims', 'previous_ending', 'revision'}
        assert fingerprint({'prompt': candidate_prompt(item, 'reduced'),
                            'schema': SegmentScript.model_json_schema()}) == item['baseline_input_hash']
        assert set(item['payload']['segment']['claim_ids']) == set(item['payload']['claims'])
        focused = json.loads(candidate_prompt(item, 'focused'))
        original = json.loads(item['baseline_prompt'])
        assert {k:v for k,v in focused.items() if k != 'instruction'} == {
            k:v for k,v in original.items() if k != 'instruction'}
        assert len(focused['instruction']) < len(original['instruction'])
        for phrase in ('claim_ids', '原文', '主持人', '嘉宾', 'segment_id', 'target_chars'):
            assert phrase in focused['instruction']
    report = baseline_report(frozen)
    assert report['totals']['reasoning_tokens'] == 6099
    assert report['totals']['output_tokens'] == 6777
    assert report['totals']['estimated_cost_cny'] == .0302
    assert [r['metrics']['generated_chars'] for r in report['dialogue']] == [133,119]


def test_frozen_asset_is_reusable_when_original_output_is_absent(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert FIXTURE.is_file()
    assert baseline_report(fixture())['dialogue'][1]['segment_id'] == '0002'


def test_offline_quality_gate_rejects_unsupported_and_role_damage():
    item = fixture()['dialogue'][0]
    baseline = form_metrics(SegmentScript.model_validate(item['baseline_output']), item)
    script = SegmentScript.model_validate(item['baseline_output']).model_copy(deep=True)
    script.turns[0].claim_ids = ['made-up-claim']
    script.turns[0].text += '2026年。'
    script.turns[1].speaker = '主持人'
    metrics = form_metrics(script, item)
    assert quality_gate(metrics, baseline)['status'] == 'failed'
    assert metrics['unsupported_claim_ids'] == ['made-up-claim']
    assert metrics['unsupported_numeric_turns'] == [0]
    assert quality_gate(baseline, baseline)['status'] == 'pending_semantic_and_listening_review'


def test_live_missing_key_fails_before_creating_receipt(tmp_path, monkeypatch):
    monkeypatch.delenv('DEEPSEEK_API_KEY', raising=False)
    with pytest.raises(RuntimeError, match='unavailable'):
        run_live(fixture(), 'reduced', tmp_path / 'candidate')
    assert not (tmp_path / 'candidate').exists()


def test_live_refuses_to_repeat_started_or_failed_receipt(tmp_path, monkeypatch):
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'test-only-offline')
    out = tmp_path / 'candidate'
    out.mkdir()
    (out / 'reduced-0001.json').write_text(json.dumps({'status':'started'}))
    with pytest.raises(RuntimeError, match='manual inspection'):
        run_live(fixture(), 'reduced', out)


def test_reduced_candidate_uses_documented_deepseek_parameter():
    spec = ProviderSpec(name='deepseek', kind='llm', type='openai-compatible',
        model='deepseek-flash', base_url='https://api.deepseek.com',
        generation=GenerationConfig(thinking='disabled'))
    assert spec.generation.request_fields() == {'thinking': {'type': 'disabled'}}
    assert 'reasoning_effort' not in spec.generation.request_fields()


def test_candidate_totals_preserve_unknown_usage_and_gate():
    rows = [{'status': 'completed', 'usage': {'input_tokens': 10, 'output_tokens': 20,
             'reasoning_tokens': None}, 'latency_seconds': 1.2, 'estimated_cost_cny': None,
             'metrics': {'generated_chars': 50, 'turn_count': 3},
             'quality_gate': {'status': 'pending_semantic_and_listening_review'}}]
    totals = candidate_totals(rows)
    assert totals['input_tokens'] == 10 and totals['output_tokens'] == 20
    assert totals['reasoning_tokens'] is None and totals['reasoning_ratio'] is None
    assert totals['estimated_cost_cny'] is None
    assert totals['quality_gate'] == 'pending_semantic_and_listening_review'


def test_experiment_final_wire_matches_historical_provider_except_intended_variable(tmp_path, monkeypatch):
    """Capture the official adapter boundary offline; never open a socket."""
    frozen = fixture()
    item = frozen['dialogue'][0]
    historical = next(p for p in frozen['cost_snapshot']['providers'] if p['name'] == 'deepseek')
    captured = []

    class WireCaptured(Exception):
        pass

    def capture(self, route, payload=None):
        captured.append((self.spec, route, payload))
        raise WireCaptured()

    monkeypatch.setattr(CompatibleLLMProvider, '_request', capture)
    with pytest.raises(WireCaptured):
        CompatibleLLMProvider(ProviderSpec(**historical)).for_task(item['task']).generate_structured(
            item['baseline_prompt'], SegmentScript)
    baseline_spec, baseline_route, baseline_body = captured.pop()
    assert baseline_route == 'chat/completions'

    monkeypatch.setenv('DEEPSEEK_API_KEY', 'test-only-offline')
    frozen['dialogue'] = [item]
    for candidate in ('reduced', 'focused'):
        with pytest.raises(RuntimeError, match='safe receipt'):
            run_live(frozen, candidate, tmp_path / candidate)
        spec, route, body = captured.pop()
        assert route == baseline_route
        assert (spec.base_url, spec.model, spec.timeout_seconds) == (
            baseline_spec.base_url, baseline_spec.model, baseline_spec.timeout_seconds)
        assert body['model'] == baseline_body['model']
        assert body['messages'][0] == baseline_body['messages'][0]
        assert body['response_format'] == baseline_body['response_format'] == {'type': 'json_object'}
        assert body['stream'] is baseline_body['stream'] is False
        assert not {'max_tokens', 'tools', 'tool_choice', 'reasoning_effort'} & body.keys()
        if candidate == 'reduced':
            assert body == {**baseline_body, 'thinking': {'type': 'disabled'}}
        else:
            assert body['messages'][1]['content'] == candidate_prompt(item, 'focused')
            assert {k: v for k, v in body.items() if k != 'messages'} == {
                k: v for k, v in baseline_body.items() if k != 'messages'}

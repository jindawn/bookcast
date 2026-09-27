"""Unit and integration tests for Two-Tier Consistency Shadow Mode."""
from pathlib import Path
import json

import pytest

from bookcast.content_models import (
    ClaimReview,
    ConsistencyReview,
    ContentOptions,
    ContentTurn,
    Segment,
    SegmentScript,
)
from bookcast.pipeline import Pipeline
from bookcast.provider_chain import ProviderChain
from bookcast.provider_config import LLMRouting, ProvidersConfig, ProviderSpec
from bookcast.providers import MockLLMProvider, MockTTSProvider
from bookcast.shadow_consistency import (
    Tier1ScreeningResult,
    tier1_screening_prompt,
    tier2_review_prompt,
    resolve_shadow_providers,
    run_shadow_consistency_for_segment,
    save_shadow_consistency_comparison,
)


@pytest.mark.unit
def test_shadow_mode_defaults_are_false():
    """Verify that shadow consistency is opt-in and defaults to False."""
    options = ContentOptions()
    assert options.consistency_shadow_mode is False

    routing = LLMRouting(
        general=['mock'],
        cheap=['mock'],
        complex=['mock'],
        high_quality=['mock'],
    )
    assert routing.consistency_shadow_mode is False


@pytest.mark.unit
def test_tier1_screening_prompt_structure():
    """Verify Tier 1 prompt only includes source turns and cited claims."""
    script = SegmentScript(
        segment_id='0001',
        title='测试片段',
        turns=[
            ContentTurn(speaker='主持人', text='这是第一轮发言。', intent='explain', claim_ids=['c1'], attribution='source'),
            ContentTurn(speaker='嘉宾', text='这是闲聊讨论。', intent='question', claim_ids=[], attribution='discussion'),
            ContentTurn(speaker='主持人', text='这是假设案例。', intent='counterexample', claim_ids=[], attribution='hypothetical'),
            ContentTurn(speaker='嘉宾', text='这是第二轮事实。', intent='explain', claim_ids=['c2'], attribution='source'),
        ]
    )
    claims = {
        'c1': {'quote': '原文证据一'},
        'c2': {'quote': '原文证据二'},
        'c3': {'quote': '未引用的证据三'},
    }
    prompt_str = tier1_screening_prompt('0001', script, claims)
    payload = json.loads(prompt_str)

    assert payload['operation'] == 'consistency_tier1'
    assert len(payload['turns']) == 2
    assert payload['turns'][0]['turn_index'] == 0
    assert payload['turns'][1]['turn_index'] == 3
    assert 'c1' in payload['claims']
    assert 'c2' in payload['claims']
    assert 'c3' not in payload['claims']  # Uncited claim should not be sent


@pytest.mark.unit
def test_tier2_review_prompt_filters_to_suspicious_turns_only():
    """Verify Tier 2 prompt only contains suspicious turns and their subset claims."""
    script = SegmentScript(
        segment_id='0001',
        title='测试片段',
        turns=[
            ContentTurn(speaker='主持人', text='第0轮事实。', intent='explain', claim_ids=['c1'], attribution='source'),
            ContentTurn(speaker='嘉宾', text='第1轮事实有疑问。', intent='explain', claim_ids=['c2'], attribution='source'),
        ]
    )
    claims = {
        'c1': {'quote': '证据一'},
        'c2': {'quote': '证据二'},
    }
    # Suspicious only turn 1
    prompt_str = tier2_review_prompt('0001', script, claims, suspicious_turn_ids=[1], revision=0)
    payload = json.loads(prompt_str)

    assert payload['operation'] == 'consistency'
    assert len(payload['script']['turns']) == 1
    assert payload['script']['turns'][0]['turn_index'] == 1
    assert 'c2' in payload['claims']
    assert 'c1' not in payload['claims']  # turn 0's claim is not sent


@pytest.mark.unit
def test_tier1_screening_result_normalization():
    """Verify robust parsing and normalization of turn IDs."""
    res1 = Tier1ScreeningResult(status='PASS', suspicious_turn_ids=[], reasons=[])
    assert res1.status == 'PASS'
    assert res1.suspicious_turn_ids == []

    # Model returning string formatted turn IDs
    data = {'status': 'REVIEW', 'suspicious_turn_ids': ['turn_4', '2', 2], 'reasons': ['bad']}
    res2 = Tier1ScreeningResult.model_validate(data)
    assert res2.status == 'REVIEW'
    assert res2.suspicious_turn_ids == [2, 4]


@pytest.mark.unit
def test_shadow_comparison_metrics_calculation(tmp_path):
    """Test aggregation and metric computation in save_shadow_consistency_comparison."""
    class DummyRunner:
        class Manifest:
            job_id = 'job_test_123'
            book_id = 'book_123'
        manifest = Manifest()
        def path(self, rel):
            p = tmp_path / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            return p

    runner = DummyRunner()

    segment_results = [
        {
            'status': 'completed',
            'segment_id': '0001',
            'production': {
                'verdict': 'supported',
                'flagged_turns': [],
                'reasoning_tokens': 1200,
                'latency_seconds': 5.0,
                'estimated_cost_cny': 0.008,
            },
            'shadow_twotier': {
                'escalated': False,
                'overall_verdict': 'supported',
                'flagged_turns': [],
                'total_tokens': {'reasoning': 0},
                'latency_seconds': 1.0,
                'estimated_cost_cny': 0.001,
            },
            'comparison': {
                'verdict_agreement': True,
                'flagged_turn_agreement': True,
                'missed_by_shadow': [],
                'shadow_only_warning': [],
                'shadow_false_negative_candidate': 0,
                'deepseek_calls_avoided': 1,
            }
        },
        {
            'status': 'completed',
            'segment_id': '0002',
            'production': {
                'verdict': 'contradicted',
                'flagged_turns': [4],
                'reasoning_tokens': 2400,
                'latency_seconds': 8.0,
                'estimated_cost_cny': 0.015,
            },
            'shadow_twotier': {
                'escalated': True,
                'overall_verdict': 'contradicted',
                'flagged_turns': [4],
                'total_tokens': {'reasoning': 200},
                'latency_seconds': 3.5,
                'estimated_cost_cny': 0.003,
            },
            'comparison': {
                'verdict_agreement': True,
                'flagged_turn_agreement': True,
                'missed_by_shadow': [],
                'shadow_only_warning': [],
                'shadow_false_negative_candidate': 0,
                'deepseek_calls_avoided': 0,
            }
        }
    ]

    summary = save_shadow_consistency_comparison(runner, segment_results)
    assert summary['acceptance_decision'] == 'SHADOW_ACCEPTED'
    metrics = summary['metrics']
    assert metrics['total_segments'] == 2
    assert metrics['shadow_false_negative_candidate'] == 0
    assert metrics['verdict_agreement_rate'] == 1.0
    assert metrics['flagged_turn_agreement_rate'] == 1.0
    assert metrics['deepseek_calls_avoided_count'] == 1
    assert metrics['deepseek_calls_avoided_rate'] == 0.5
    assert metrics['tier1_escalation_rate'] == 0.5
    assert metrics['production_reasoning_tokens'] == 3600
    assert metrics['shadow_reasoning_tokens'] == 200
    assert metrics['reasoning_reduction_percent'] == pytest.approx(94.44, rel=1e-2)
    assert metrics['counterfactual_production_cost']['full_deepseek_cost_cny'] == 0.023
    assert metrics['counterfactual_production_cost']['twotier_cost_cny'] == 0.004
    assert metrics['counterfactual_production_cost']['expected_savings_cny'] == 0.019
    assert metrics['counterfactual_production_cost']['expected_savings_percent'] == pytest.approx(82.61, rel=1e-2)


@pytest.mark.unit
def test_shadow_comparison_detects_false_negative_rejection(tmp_path):
    """Verify that if shadow misses a production flagged turn, acceptance_decision is SHADOW_REJECTED."""
    class DummyRunner:
        class Manifest:
            job_id = 'job_fn_test'
            book_id = 'book_fn'
        manifest = Manifest()
        def path(self, rel):
            p = tmp_path / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            return p

    runner = DummyRunner()

    segment_results = [
        {
            'status': 'completed',
            'segment_id': '0001',
            'production': {
                'verdict': 'contradicted',
                'flagged_turns': [2],
                'reasoning_tokens': 1200,
                'latency_seconds': 5.0,
                'estimated_cost_cny': 0.008,
            },
            'shadow_twotier': {
                'escalated': False,
                'overall_verdict': 'supported',
                'flagged_turns': [],
                'total_tokens': {'reasoning': 0},
                'latency_seconds': 1.0,
                'estimated_cost_cny': 0.001,
            },
            'comparison': {
                'verdict_agreement': False,
                'flagged_turn_agreement': False,
                'missed_by_shadow': [2],
                'shadow_only_warning': [],
                'shadow_false_negative_candidate': 1,  # Missed!
                'deepseek_calls_avoided': 1,
            }
        }
    ]

    summary = save_shadow_consistency_comparison(runner, segment_results)
    assert summary['acceptance_decision'] == 'SHADOW_REJECTED'
    assert summary['metrics']['shadow_false_negative_candidate'] == 1


@pytest.mark.integration
def test_mock_pipeline_with_shadow_consistency_mode(tmp_path):
    """Verify end-to-end mock generation with consistency_shadow_mode=True."""
    txt_file = tmp_path / 'sample.txt'
    txt_file.write_text('第一章 绪论\n\n这是关于科学哲学的一段文字。它讨论了实证与逻辑的关系。', encoding='utf-8')

    output_dir = tmp_path / 'output'
    llm = ProviderChain([MockLLMProvider()])
    tts = ProviderChain([MockTTSProvider()])
    pipeline = Pipeline(llm, tts, output_dir)

    job_root = pipeline.generate(txt_file, consistency_shadow_mode=True)

    # 1. Manifest verification: status is completed and content_options records shadow mode
    manifest_path = job_root / 'manifest.json'
    assert manifest_path.is_file()
    manifest_data = json.loads(manifest_path.read_text(encoding='utf-8'))
    assert manifest_data['status'] == 'completed'
    assert manifest_data['content_options']['consistency_shadow_mode'] is True

    # 2. Manifest ai_calls has NO shadow calls (isolation)
    shadow_calls = [c for c in manifest_data['ai_calls'] if 'shadow' in c['task']]
    assert len(shadow_calls) == 0

    # 3. Shadow artifacts were written
    comparison_file = job_root / 'evaluation/shadow_consistency_comparison.json'
    assert comparison_file.is_file()
    comp_data = json.loads(comparison_file.read_text(encoding='utf-8'))
    assert comp_data['schema_version'] == 1
    assert comp_data['acceptance_decision'] == 'SHADOW_ACCEPTED'
    assert comp_data['metrics']['shadow_false_negative_candidate'] == 0

    # 4. Check segment shadow file
    segment_shadow = job_root / 'evaluation/shadow/0001.json'
    assert segment_shadow.is_file()
    seg_data = json.loads(segment_shadow.read_text(encoding='utf-8'))
    assert seg_data['shadow_twotier']['tier1_status'] == 'REVIEW'
    assert seg_data['shadow_twotier']['escalated'] is True
    assert seg_data['comparison']['verdict_agreement'] is True


@pytest.mark.integration
def test_shadow_consistency_exception_tolerance(tmp_path, monkeypatch):
    """Verify that an exception inside shadow consistency does not fail the main pipeline."""
    from unittest.mock import patch
    import bookcast.shadow_consistency as sc

    txt_file = tmp_path / 'sample.txt'
    txt_file.write_text('第一章 绪论\n\n测试文本。', encoding='utf-8')
    output_dir = tmp_path / 'output'

    # Simulate an unexpected failure in tier 1 screening
    def buggy_run_shadow(*args, **kwargs):
        raise RuntimeError("Simulated shadow consistency failure")

    with patch('bookcast.shadow_consistency.run_shadow_consistency_for_segment', side_effect=buggy_run_shadow):
        pipeline = Pipeline(ProviderChain([MockLLMProvider()]), ProviderChain([MockTTSProvider()]), output_dir)
        # Should NOT raise, job should succeed
        job_root = pipeline.generate(txt_file, consistency_shadow_mode=True)
        manifest_data = json.loads((job_root / 'manifest.json').read_text(encoding='utf-8'))
        assert manifest_data['status'] == 'completed'


@pytest.mark.integration
def test_cli_shadow_consistency_flag(tmp_path):
    """Verify CLI --shadow-consistency passes shadow flag to the pipeline."""
    from typer.testing import CliRunner
    from bookcast.cli import app

    txt_file = tmp_path / 'test.txt'
    txt_file.write_text('第一章 测试\n\n这是一段测试内容。', encoding='utf-8')
    output_dir = tmp_path / 'output'

    config_path = tmp_path / 'mock.toml'
    config_path.write_text('''
schema_version = 1
llm_priority = ["mock"]
tts_priority = ["mock-tts"]

[[providers]]
name = "mock"
kind = "llm"
type = "mock"
model = "mock-llm-v1"

[[providers]]
name = "mock-tts"
kind = "tts"
type = "mock"
model = "mock-tones-v1"
''', encoding='utf-8')

    runner = CliRunner()
    result = runner.invoke(app, [
        'generate',
        str(txt_file),
        '--config', str(config_path),
        '--output-dir', str(output_dir),
        '--shadow-consistency',
    ])
    assert result.exit_code == 0, result.output
    # Verify shadow comparison was generated
    comp_files = list(output_dir.glob('*/evaluation/shadow_consistency_comparison.json'))
    assert len(comp_files) == 1
    assert comp_files[0].is_file()

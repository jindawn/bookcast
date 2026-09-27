"""Offline tests for Phase 19.3B Consistency Experiment: fixtures, Two-Tier routing, metrics and safety."""
from datetime import datetime, timezone
import json
from pathlib import Path
import pytest

from bookcast.content_models import ClaimReview, ConsistencyReview, SegmentScript
from bookcast.generation import GenerationConfig, ProviderUsage
from bookcast.storage import fingerprint
from scripts.llm_reasoning_consistency import (
    FIXTURE_PATH,
    Tier1ScreeningResult,
    baseline_prompt,
    calculate_detection_metrics,
    estimate_cost_for_model,
    evaluate_two_tier_offline,
    load_fixture,
    reduced_prompt,
    run_offline_benchmark,
    tier1_screening_prompt,
    tier2_review_prompt,
)


def test_consistency_fixture_contract():
    data = load_fixture()
    assert data["schema_version"] == 1
    assert data["source_sha256"] == "b30a16bd39cf8924da501dfa005be4bc9eea549045f08dd597669bde152dde94"

    # Historical Baseline totals match Phase 19.2 measured E2E
    base = data["baseline_totals"]
    assert base["calls"] == 2
    assert base["input_tokens"] == 3147
    assert base["output_tokens"] == 5388
    assert base["reasoning_tokens"] == 5066
    assert base["reasoning_ratio"] == 0.9402
    assert abs(base["latency_seconds"] - 24.975) < 0.01
    assert base["estimated_cost_cny"] == 0.0246

    fixtures = data["fixtures"]
    assert len(fixtures) == 10

    # Verify all 7 required categories are covered
    categories = {f["category"] for f in fixtures}
    required_categories = {
        "completely_correct",
        "minor_unsupported_elaboration",
        "obvious_hallucination",
        "numerical_error",
        "incorrect_attribution",
        "missing_critical_qualifier",
        "obvious_contradiction",
    }
    assert required_categories.issubset(categories)

    for item in fixtures:
        script = SegmentScript.model_validate(item["script"])
        assert script.segment_id == item["segment_id"]
        gt = item["ground_truth"]
        assert isinstance(gt["has_error"], bool)
        assert gt["expected_tier1_status"] in ("PASS", "REVIEW")
        assert isinstance(gt["flawed_turn_indices"], list)
        assert isinstance(gt["expected_verdicts"], dict)

        # Check turn index alignment
        source_turns = {
            i for i, t in enumerate(script.turns) if t.attribution == "source"
        }
        for turn_idx_str in gt["expected_verdicts"].keys():
            assert int(turn_idx_str) in source_turns

    # Verify historical segment prompts match manifest input_hashes
    case8 = next(f for f in fixtures if f["id"] == "case_8_historical_segment_0001")
    p8 = baseline_prompt(case8)
    schema = ConsistencyReview.model_json_schema()
    assert fingerprint({"prompt": p8, "schema": schema}) == "a78d4d23a839185ae83b5832db8d28e37f61ad6e962422cb910e86c68bf26dd2"

    case9 = next(f for f in fixtures if f["id"] == "case_9_historical_segment_0002")
    p9 = baseline_prompt(case9)
    assert fingerprint({"prompt": p9, "schema": schema}) == "e4f507bc57e6ab5472b876cd79d8cb799c7da9e3303efc25d6c527b7514f3b21"


def test_tier1_screening_result_schema_strictness():
    # Valid PASS
    pass_res = Tier1ScreeningResult(status="PASS", suspicious_turn_ids=[], reasons=[])
    assert pass_res.status == "PASS"
    assert pass_res.suspicious_turn_ids == []

    # Valid REVIEW
    rev_res = Tier1ScreeningResult(status="REVIEW", suspicious_turn_ids=[0, 4], reasons=["Numerical error in turn 4"])
    assert rev_res.status == "REVIEW"
    assert rev_res.suspicious_turn_ids == [0, 4]

    # Extra fields are forbidden
    with pytest.raises(Exception):
        Tier1ScreeningResult(status="PASS", extra_field="forbidden")

    # Invalid status is rejected
    with pytest.raises(Exception):
        Tier1ScreeningResult(status="INVALID", suspicious_turn_ids=[])


def test_tier1_prompt_is_isolated_and_lean():
    fixtures = load_fixture()["fixtures"]
    fixture_item = fixtures[0]
    prompt_str = tier1_screening_prompt(fixture_item)
    payload = json.loads(prompt_str)

    assert "instruction" in payload
    assert "turns" in payload
    assert "claims" in payload

    # Ensure no whole-book or irrelevant metadata is leaked
    assert "plan" not in payload
    assert "chapters" not in payload
    assert "source_filter" not in payload

    # Verify instruction emphasizes the 5 targeted risk categories
    instr = payload["instruction"]
    for risk in ("unsupported factual claim", "contradiction", "incorrect attribution",
                 "materially distorted meaning", "missing critical qualifier"):
        assert risk in instr

    # Verify only attribution=source turns are submitted to Tier 1
    script = fixture_item["script"]
    expected_source_indices = [
        idx for idx, turn in enumerate(script["turns"]) if turn.get("attribution") == "source"
    ]
    prompt_turn_indices = [t["turn_index"] for t in payload["turns"]]
    assert prompt_turn_indices == expected_source_indices


def test_tier2_review_prompt_only_sends_suspicious_turns():
    fixtures = load_fixture()["fixtures"]
    # Case 4 has numerical error in turn 4
    case4 = next(f for f in fixtures if f["id"] == "case_4_numerical_error")
    suspicious_ids = [4]

    prompt_str = tier2_review_prompt(case4, suspicious_ids)
    payload = json.loads(prompt_str)

    assert payload["segment_id"] == "0001"
    reviewed_turns = payload["script"]["turns"]
    assert len(reviewed_turns) == 1
    assert reviewed_turns[0]["turn_index"] == 4

    # Turns 0, 1, 2, 3 must NOT be present in Tier 2
    for forbidden_idx in (0, 1, 2, 3):
        assert not any(t["turn_index"] == forbidden_idx for t in reviewed_turns)

    # Claims must only include those cited by turn 4
    cited_by_turn4 = set(reviewed_turns[0]["claim_ids"])
    for cid in payload["claims"].keys():
        assert cid in cited_by_turn4


def test_two_tier_routing_pass_bypasses_deepseek():
    fixtures = load_fixture()["fixtures"]
    case1 = next(f for f in fixtures if f["id"] == "case_1_completely_correct")

    res = evaluate_two_tier_offline(case1)
    assert res["escalated"] is False
    assert res["qwen_calls"] == 1
    assert res["deepseek_calls"] == 0
    assert res["tier1"]["status"] == "PASS"
    assert res["tier2"] is None
    assert res["detected_error"] is False

    # All source checks are preserved and marked supported
    assert len(res["checks"]) == 4
    assert all(c["verdict"] == "supported" for c in res["checks"])


def test_two_tier_routing_review_escalates_and_merges():
    fixtures = load_fixture()["fixtures"]
    case4 = next(f for f in fixtures if f["id"] == "case_4_numerical_error")

    res = evaluate_two_tier_offline(case4)
    assert res["escalated"] is True
    assert res["qwen_calls"] == 1
    assert res["deepseek_calls"] == 1
    assert res["tier1"]["status"] == "REVIEW"
    assert res["tier2"] is not None
    assert res["tier2"]["reviewed_turns"] == [4]
    assert res["detected_error"] is True

    # Merged checks have supported for turns 0 and 2, and contradicted for turn 4
    checks_map = {c["turn_index"]: c["verdict"] for c in res["checks"]}
    assert checks_map[0] == "supported"
    assert checks_map[2] == "supported"
    assert checks_map[4] == "contradicted"


def test_detection_quality_metrics_calculation():
    fixtures = [
        {"id": "f1", "category": "clean", "description": "clean", "ground_truth": {"has_error": False, "flawed_turn_indices": []}},
        {"id": "f2", "category": "bad", "description": "bad", "ground_truth": {"has_error": True, "flawed_turn_indices": [1]}},
        {"id": "f3", "category": "bad2", "description": "bad2", "ground_truth": {"has_error": True, "flawed_turn_indices": [2]}},
        {"id": "f4", "category": "clean2", "description": "clean2", "ground_truth": {"has_error": False, "flawed_turn_indices": []}},
    ]
    # Results:
    # f1: clean, detected_error=False -> TN
    # f2: bad, detected_error=True -> TP
    # f3: bad2, detected_error=False -> FN (missed!)
    # f4: clean2, detected_error=True -> FP (false alarm!)
    results = [
        {"fixture_id": "f1", "detected_error": False},
        {"fixture_id": "f2", "detected_error": True},
        {"fixture_id": "f3", "detected_error": False},
        {"fixture_id": "f4", "detected_error": True},
    ]

    metrics = calculate_detection_metrics(results, fixtures)
    assert metrics["true_positives"] == 1
    assert metrics["true_negatives"] == 1
    assert metrics["false_positives"] == 1
    assert metrics["false_negatives"] == 1
    assert metrics["precision"] == 0.5  # 1 / (1 + 1)
    assert metrics["recall"] == 0.5     # 1 / (1 + 1)
    assert len(metrics["false_negative_details"]) == 1
    assert metrics["false_negative_details"][0]["fixture_id"] == "f3"


def test_cost_estimation_for_qwen_and_deepseek():
    data = load_fixture()
    price_config = data["cost_snapshot"]

    # Qwen usage
    qwen_usage = ProviderUsage(input_tokens=1000, output_tokens=100, reasoning_tokens=0, cache_hit_tokens=200)
    now = datetime(2026, 9, 27, 10, 0, 0, tzinfo=timezone.utc)
    cost_qwen = estimate_cost_for_model("qwen3.7-flash", qwen_usage, now, price_config)
    assert cost_qwen is not None
    # 800 uncached * 1.0 + 200 cached * 0.2 + 100 out * 2.0 = 800 + 40 + 200 = 1040 / 1M = 0.00104 CNY
    assert cost_qwen == 0.00104

    # DeepSeek usage
    ds_usage = ProviderUsage(input_tokens=1000, output_tokens=2000, reasoning_tokens=1800, cache_hit_tokens=0)
    cost_ds = estimate_cost_for_model("deepseek-flash", ds_usage, now, price_config)
    assert cost_ds is not None
    assert cost_ds > 0.0


def test_run_offline_benchmark_aggregates():
    fixtures_data = load_fixture()
    report = run_offline_benchmark(fixtures_data)

    assert "candidate_A_baseline_historical" in report
    assert "candidate_C_two_tier" in report

    c_report = report["candidate_C_two_tier"]
    assert c_report["total_fixtures"] == 10
    assert c_report["qwen_calls"] == 10
    assert c_report["deepseek_calls"] == 7
    assert c_report["escalation_rate"] == 0.7
    assert c_report["deepseek_calls_avoided_pct"] == 30.0
    assert c_report["detection_quality"]["recall"] == 1.0
    assert c_report["detection_quality"]["false_negatives"] == 0
    assert c_report["schema_validity"] is True


def test_reduced_reasoning_prompt_structure():
    fixtures = load_fixture()["fixtures"]
    p = reduced_prompt(fixtures[0])
    payload = json.loads(p)
    assert "operation" in payload and payload["operation"] == "consistency"
    assert "5类风险" in payload["instruction"]
    assert "无需冗长推导" in payload["instruction"]


def test_live_execution_fails_safely_without_keys(monkeypatch, tmp_path):
    from scripts.llm_reasoning_consistency import execute_two_tier_live
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    fixtures = load_fixture()["fixtures"]
    with pytest.raises(RuntimeError, match="Both DASHSCOPE_API_KEY and DEEPSEEK_API_KEY must be configured"):
        execute_two_tier_live(fixtures[0], tmp_path, {})
    assert not (tmp_path / f"twotier-{fixtures[0]['id']}.json").exists()


def test_live_execution_refuses_automatic_retry_on_existing_receipt(monkeypatch, tmp_path):
    from scripts.llm_reasoning_consistency import execute_two_tier_live
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-mock-key")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-mock-key")

    fixtures = load_fixture()["fixtures"]
    fid = fixtures[0]["id"]
    dest = tmp_path / f"twotier-{fid}.json"
    dest.write_text(json.dumps({"status": "failed", "prompt_hash": "stale"}))

    with pytest.raises(RuntimeError, match="requires manual inspection; refusing automatic retry"):
        execute_two_tier_live(fixtures[0], tmp_path, {})


def test_production_router_and_dialogue_unaffected():
    """Ensure production model_routing, content instructions and dialogue configs remain pristine."""
    from bookcast.content import INSTRUCTIONS
    from bookcast.model_routing import RoutedLLMChain
    from bookcast.generation import POLICY

    # Production consistency instruction unchanged
    assert "逐一检查 script.turns 中 attribution=source 的发言" in INSTRUCTIONS["consistency"]

    # Production task policy unchanged
    assert POLICY["consistency"].thinking == "enabled"
    assert POLICY["consistency"].reasoning_effort == "low"
    assert POLICY["dialogue"].thinking == "enabled"
    assert POLICY["dialogue"].reasoning_effort == "low"

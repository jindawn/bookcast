"""R5: Usage and Physical Cost Telemetry tests.

Tests separation of logical attempts and physical HTTP wire requests,
cost summary resolution with TTS quality routing, and accurate status
accounting (unavailable/partial instead of false 0 cost).
"""

import io
import json
import base64
from pathlib import Path

import pytest

from bookcast.adapters.qwen_cloud import QwenCloudTTSProvider
from bookcast.cost import calculate_cost_summary, _selected
from bookcast.models import AIAttempt
from bookcast.provider_api import ErrorKind, ProviderError, ProviderRequestContext, SpeechUnit
from bookcast.provider_config import ProvidersConfig, ProviderSpec, TTSRouting


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _raw_pcm(nframes: int = 480) -> bytes:
    return b"\x00\x00" * nframes


def _make_response(pcm: bytes) -> bytes:
    b64 = base64.b64encode(pcm).decode()
    return json.dumps({"output": {"audio": b64}, "request_id": "test-id"}).encode()


def make_routed_config() -> ProvidersConfig:
    return ProvidersConfig.model_validate({
        "providers": [
            {"name": "mock-llm", "kind": "llm", "type": "mock", "model": "mock-llm-v1"},
            {"name": "qwen-tts", "kind": "tts", "type": "mock", "model": "cosyvoice-v2"},
            {"name": "gemini-tts", "kind": "tts", "type": "mock", "model": "gemini-3.8-flash-lite-tts"},
        ],
        "llm_priority": ["mock-llm"],
        "tts_priority": ["qwen-tts", "gemini-tts"],
        "tts_routing": {
            "standard": "qwen-tts",
            "high_quality": "gemini-tts",
            "default_quality": "standard",
        },
    })


# ---------------------------------------------------------------------------
# 1. _selected with TTS routing
# ---------------------------------------------------------------------------

class TestCostSelectedWithRouting:

    def test_selected_auto_resolves_standard_provider(self):
        config = make_routed_config()
        settings = {"tts_selection": "auto"}
        name, model = _selected(settings, config, "tts")
        assert name == "qwen-tts"
        assert model == "cosyvoice-v2"

    def test_selected_standard_resolves_standard(self):
        config = make_routed_config()
        settings = {"tts_selection": "standard"}
        name, model = _selected(settings, config, "tts")
        assert name == "qwen-tts"
        assert model == "cosyvoice-v2"

    def test_selected_high_resolves_high_quality(self):
        config = make_routed_config()
        settings = {"tts_selection": "high"}
        name, model = _selected(settings, config, "tts")
        assert name == "gemini-tts"
        assert model == "gemini-3.8-flash-lite-tts"

    def test_selected_override_resolves_named_provider(self):
        config = make_routed_config()
        settings = {"tts_selection": "gemini-tts"}
        name, model = _selected(settings, config, "tts")
        assert name == "gemini-tts"
        assert model == "gemini-3.8-flash-lite-tts"


# ---------------------------------------------------------------------------
# 2. Qwen Cloud TTS Physical Telemetry
# ---------------------------------------------------------------------------

class TestQwenCloudPhysicalTelemetry:

    def _spec(self):
        return ProviderSpec.model_validate({
            "name": "qwen-cloud", "kind": "tts", "type": "qwen-cloud-tts",
            "model": "cosyvoice-v2", "api_key_env": "DASHSCOPE_API_KEY",
            "qwen_cloud_tts": {
                "send_text_to_cloud": True,
                "host_voice": "longhua", "guest_voice": "longyue",
                "min_request_interval": 0,
            },
        })

    def test_successful_request_writes_physical_telemetry(self, monkeypatch, tmp_path):
        spec = self._spec()
        provider = QwenCloudTTSProvider(spec, sleeper=lambda _: None)
        monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")

        class FakeOpener:
            def open(self, req, timeout):
                return io.BytesIO(_make_response(_raw_pcm()))

        monkeypatch.setattr("bookcast.adapters.qwen_cloud.request.build_opener", lambda *args: FakeOpener())

        telemetry_file = tmp_path / "usage" / "physical_requests.jsonl"
        context = ProviderRequestContext(
            job_id="job-1", output_id="out-1",
            logical_chunk_id="tts:0001:0001", provider=spec.name, model=spec.model,
            telemetry_path=telemetry_file,
        )

        unit = SpeechUnit(speaker="主持人", text="测试语句")
        dest = tmp_path / "out.wav"
        provider.synthesize_unit_with_context(unit, dest, context=context)

        assert dest.exists()
        assert telemetry_file.exists()
        lines = [json.loads(line) for line in telemetry_file.read_text().splitlines() if line.strip()]
        assert len(lines) == 1
        rec = lines[0]
        assert rec["job_id"] == "job-1"
        assert rec["logical_chunk_id"] == "tts:0001:0001"
        assert rec["provider"] == "qwen-cloud"
        assert rec["model"] == "cosyvoice-v2"
        assert rec["http_status"] == 200
        assert rec["result"] == "succeeded"
        assert rec["usage_available"] is True
        assert rec["billing_evidence"] == "dashscope_tts"

    def test_failed_request_writes_failure_telemetry(self, monkeypatch, tmp_path):
        from urllib import error as urlerr
        spec = self._spec()
        provider = QwenCloudTTSProvider(spec, sleeper=lambda _: None)
        monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")

        class FailingOpener:
            def open(self, req, timeout):
                raise urlerr.HTTPError(
                    url="", code=429, msg="Rate limit",
                    hdrs={}, fp=io.BytesIO(json.dumps({"code": "Throttling"}).encode()),
                )

        monkeypatch.setattr("bookcast.adapters.qwen_cloud.request.build_opener", lambda *args: FailingOpener())

        telemetry_file = tmp_path / "usage" / "physical_requests.jsonl"
        context = ProviderRequestContext(
            job_id="job-1", output_id="out-1",
            logical_chunk_id="tts:0001:0001", provider=spec.name, model=spec.model,
            telemetry_path=telemetry_file,
        )

        unit = SpeechUnit(speaker="主持人", text="测试语句")
        dest = tmp_path / "out.wav"
        with pytest.raises(ProviderError) as exc:
            provider.synthesize_unit_with_context(unit, dest, context=context)
        assert exc.value.kind == ErrorKind.RATE_LIMIT

        assert telemetry_file.exists()
        lines = [json.loads(line) for line in telemetry_file.read_text().splitlines() if line.strip()]
        assert len(lines) == 1
        rec = lines[0]
        assert rec["result"] == "failed"
        assert rec["http_status"] == 429
        assert rec["error_kind"] == "rate_limit"


# ---------------------------------------------------------------------------
# 3. Unpriced models cost accounting
# ---------------------------------------------------------------------------

class TestUnpricedModelsCostAccounting:

    def test_unpriced_model_is_unavailable_and_partial(self):
        """When an LLM model has calls but no pricing entry in config,
        amount is 0.0 but status is 'unavailable' and total status is 'partial' or 'unavailable'."""
        config = ProvidersConfig.model_validate({
            "providers": [
                {"name": "qwen", "kind": "llm", "type": "qwen-llm", "model": "qwen3-7-flash", "base_url": "https://example.com"},
                {"name": "mock-tts", "kind": "tts", "type": "mock", "model": "mock-tones-v1"},
            ],
            "llm_priority": ["qwen"],
            "tts_priority": ["mock-tts"],
            # pricing is empty: no pricing for qwen3-7-flash
        })
        from bookcast.generation import ProviderUsage
        calls = [
            AIAttempt(
                id="call-1", task="analysis:0001:0001", kind="llm", provider="qwen",
                model="qwen3-7-flash", prompt_version="v1", input_hash="a" * 64,
                status="completed",
                provider_reported_usage=ProviderUsage(input_tokens=100, output_tokens=50, cache_hit_tokens=0),
            )
        ]
        summary = calculate_cost_summary(calls, config)
        qwen_row = summary["llm"]["providers"]["qwen::qwen3-7-flash"]
        assert qwen_row["cost"]["amount"] == 0.0
        assert qwen_row["cost"]["status"] == "unavailable"
        assert summary["total"]["status"] in ("unavailable", "partial")
        assert summary["total"]["known_amount"] == 0.0


# ---------------------------------------------------------------------------
# 4. Phase 19.2: Central Cloud Pricing & Cost Accounting
# ---------------------------------------------------------------------------

class TestPhase192CloudPricing:

    def test_qwen_llm_pricing_estimated_cost(self):
        """Qwen3.7-Flash pricing: 0.20 cached, 1.00 uncached, 2.00 output per million."""
        from bookcast.generation import ProviderUsage
        config = ProvidersConfig.model_validate({
            "providers": [
                {"name": "qwen", "kind": "llm", "type": "qwen-llm", "model": "qwen3.7-flash", "base_url": "https://example.com"},
                {"name": "mock-tts", "kind": "tts", "type": "mock", "model": "mock-tones-v1"},
            ],
            "llm_priority": ["qwen"],
            "tts_priority": ["mock-tts"],
            "pricing": {
                "qwen3.7-flash": {
                    "default": {
                        "cached_input_per_million": 0.20,
                        "uncached_input_per_million": 1.00,
                        "output_per_million": 2.00,
                    }
                }
            }
        })
        calls = [
            AIAttempt(
                id="call-qwen-1", task="analysis:0001:0001", kind="llm", provider="qwen",
                model="qwen3.7-flash", prompt_version="v1", input_hash="a" * 64,
                status="completed",
                # 1000 input tokens (200 cached, 800 uncached), 500 output tokens
                # Cached: 200 * 0.20 / 1M = 0.00004
                # Uncached: 800 * 1.00 / 1M = 0.0008
                # Output: 500 * 2.00 / 1M = 0.001
                # Total = 0.00184 -> rounded to 0.0018
                provider_reported_usage=ProviderUsage(input_tokens=1000, output_tokens=500, cache_hit_tokens=200),
            )
        ]
        summary = calculate_cost_summary(calls, config)
        qwen_row = summary["llm"]["providers"]["qwen::qwen3.7-flash"]
        assert qwen_row["cost"]["amount"] == 0.0018
        assert qwen_row["estimated_cost"] == 0.0018
        assert qwen_row["actual_cost"] is None

    def test_qwen_tts_character_pricing_estimated_cost(self, tmp_path):
        """Qwen3-TTS-Instruct-Flash pricing: 80.00 per million characters (0.80 CNY / 10k chars)."""
        from bookcast.generation import ProviderUsage
        config = ProvidersConfig.model_validate({
            "providers": [
                {"name": "mock-llm", "kind": "llm", "type": "mock", "model": "mock-llm-v1"},
                {"name": "qwen-tts", "kind": "tts", "type": "mock", "model": "qwen3-tts-instruct-flash"},
            ],
            "llm_priority": ["mock-llm"],
            "tts_priority": ["qwen-tts"],
            "pricing": {
                "qwen3-tts-instruct-flash": {
                    "default": {
                        "characters_per_million": 80.00,
                    }
                }
            }
        })
        # 5000 characters: 5000 * 80.00 / 1M = 0.40 CNY
        calls = [
            AIAttempt(
                id="call-tts-1", task="tts:0001:0001", kind="tts", provider="qwen-tts",
                model="qwen3-tts-instruct-flash", prompt_version="v1", input_hash="b" * 64,
                status="completed",
                provider_reported_usage=ProviderUsage(input_tokens=5000, output_tokens=0),
            )
        ]
        summary = calculate_cost_summary(calls, config, root_dir=tmp_path)
        tts_row = summary["tts"]["providers"]["qwen-tts::qwen3-tts-instruct-flash"]
        assert tts_row["cost"]["amount"] == 0.40
        assert tts_row["estimated_cost"] == 0.40
        assert tts_row["actual_cost"] is None
        assert tts_row["cost"]["status"] == "estimated"

    def test_gemini_tts_duration_pricing_estimated_cost(self, tmp_path):
        """Gemini 3.8 Flash-Lite TTS: duration and token pricing."""
        from bookcast.generation import ProviderUsage
        config = ProvidersConfig.model_validate({
            "providers": [
                {"name": "mock-llm", "kind": "llm", "type": "mock", "model": "mock-llm-v1"},
                {"name": "gemini-tts", "kind": "tts", "type": "mock", "model": "gemini-3.8-flash-lite-tts"},
            ],
            "llm_priority": ["mock-llm"],
            "tts_priority": ["gemini-tts"],
            "pricing": {
                "gemini-3.8-flash-lite-tts": {
                    "default": {
                        "characters_per_million": 15.00,
                        "audio_seconds_per_million": 250.00,
                    }
                }
            }
        })
        # Unit sidecar with duration and character count
        unit_json = tmp_path / "audio" / "units" / "0001-0001.json"
        unit_json.parent.mkdir(parents=True, exist_ok=True)
        unit_json.write_text(json.dumps({
            "duration_seconds": 60.0,
            "character_count": 200,
        }))

        calls = [
            AIAttempt(
                id="call-gemini-1", task="tts:0001:0001", kind="tts", provider="gemini-tts",
                model="gemini-3.8-flash-lite-tts", prompt_version="v1", input_hash="c" * 64,
                status="completed",
                artifacts={"audio/units/0001-0001.json": "hash"},
                provider_reported_usage=ProviderUsage(input_tokens=200, output_tokens=0),
            )
        ]
        summary = calculate_cost_summary(calls, config, root_dir=tmp_path)
        gemini_row = summary["tts"]["providers"]["gemini-tts::gemini-3.8-flash-lite-tts"]
        # 200 chars * 15.0 / 1M = 0.003
        assert gemini_row["cost"]["amount"] == 0.003
        assert gemini_row["estimated_cost"] == 0.003
        assert gemini_row["actual_cost"] is None

    def test_deepseek_chat_and_flash_peak_off_peak(self):
        """DeepSeek chat off-peak and peak pricing."""
        from bookcast.generation import ProviderUsage
        config = ProvidersConfig.model_validate({
            "providers": [
                {"name": "deepseek", "kind": "llm", "type": "openai-compatible", "model": "deepseek-chat", "base_url": "https://api.deepseek.com"},
                {"name": "mock-tts", "kind": "tts", "type": "mock", "model": "mock-tones-v1"},
            ],
            "llm_priority": ["deepseek"],
            "tts_priority": ["mock-tts"],
            "pricing": {
                "deepseek-chat": {
                    "off_peak": {"cached_input_per_million": 0.02, "uncached_input_per_million": 1.0, "output_per_million": 4.0},
                    "peak": {"cached_input_per_million": 0.04, "uncached_input_per_million": 2.0, "output_per_million": 8.0}
                }
            }
        })
        # Off-peak: 16:30 UTC = 00:30 Beijing
        calls_off = [
            AIAttempt(
                id="c1", task="dialogue", kind="llm", provider="deepseek", model="deepseek-chat",
                prompt_version="v1", input_hash="d" * 64, status="completed",
                timestamp="2026-09-28T16:30:00Z",
                provider_reported_usage=ProviderUsage(input_tokens=1_000_000, output_tokens=1_000_000, cache_hit_tokens=500_000),
            )
        ]
        summary = calculate_cost_summary(calls_off, config)
        # 500k cached * 0.02 + 500k uncached * 1.0 + 1M output * 4.0 = 0.01 + 0.50 + 4.00 = 4.51
        assert summary["llm"]["providers"]["deepseek::deepseek-chat"]["cost"]["amount"] == 4.51
        assert summary["llm"]["providers"]["deepseek::deepseek-chat"]["estimated_cost"] == 4.51
        assert summary["llm"]["providers"]["deepseek::deepseek-chat"]["actual_cost"] is None

    def test_compatible_llm_physical_request_logging_all_15_fields(self, monkeypatch, tmp_path):
        """LLM calls must log all 15 required physical request telemetry fields to physical_requests.jsonl."""
        from bookcast.adapters.compatible import CompatibleLLMProvider
        from bookcast.provider_config import ProviderSpec
        spec = ProviderSpec(
            name="deepseek", kind="llm", type="openai-compatible",
            model="deepseek-flash", base_url="https://api.deepseek.com",
            api_key_env="DEEPSEEK_API_KEY",
        )
        monkeypatch.setenv("DEEPSEEK_API_KEY", "secret-test-key")

        response_body = {
            "id": "chatcmpl-test",
            "model": "deepseek-flash",
            "choices": [{"message": {"role": "assistant", "content": "test answer"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}
        }

        class MockOpener:
            def open(self, req, timeout):
                return io.BytesIO(json.dumps(response_body).encode("utf-8"))

        monkeypatch.setattr("bookcast.adapters.compatible.request.build_opener", lambda *args: MockOpener())

        telemetry_file = tmp_path / "usage" / "physical_requests.jsonl"
        context = ProviderRequestContext(
            job_id="job-llm-1", output_id="out-llm-1",
            logical_chunk_id="analysis:0001", provider=spec.name, model=spec.model,
            telemetry_path=telemetry_file, physical_attempt_index=0,
        )

        provider = CompatibleLLMProvider(spec)
        provider.set_context(context)
        out = provider.generate("hello world")
        assert out == "test answer"

        assert telemetry_file.exists()
        lines = [json.loads(line) for line in telemetry_file.read_text().splitlines() if line.strip()]
        assert len(lines) == 1
        rec = lines[0]

        # Verify all 15 required fields:
        assert rec["provider"] == "deepseek"
        assert rec["model"] == "deepseek-flash"
        assert rec["endpoint"] == "https://api.deepseek.com/chat/completions"
        assert "secret-test-key" not in rec["endpoint"]
        assert rec["logical_chunk_id"] == "analysis:0001"
        assert rec["physical_attempt_index"] == 0
        assert "started_at" in rec and "T" in rec["started_at"]
        assert "finished_at" in rec and "T" in rec["finished_at"]
        assert "latency" in rec and isinstance(rec["latency"], (int, float))
        assert rec["http_status"] == 200
        assert rec["result"] == "succeeded"
        assert rec["retry_reason"] is None
        assert rec["retryable"] is False
        assert rec["usage_available"] is True
        assert rec["billing_evidence"] == "openai-compatible_http"
        assert rec["error_kind"] is None

    def test_compatible_llm_physical_request_logging_http_failure(self, monkeypatch, tmp_path):
        """LLM failures log failure status, error kind, and retryability to physical_requests.jsonl."""
        from urllib import error as urlerr
        from bookcast.adapters.compatible import CompatibleLLMProvider
        from bookcast.provider_config import ProviderSpec
        spec = ProviderSpec(
            name="qwen", kind="llm", type="qwen-llm",
            model="qwen3.7-flash", base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            api_key_env="DASHSCOPE_API_KEY",
        )
        monkeypatch.setenv("DASHSCOPE_API_KEY", "secret-qwen-key")

        class FailingOpener:
            def open(self, req, timeout):
                raise urlerr.HTTPError(
                    url="", code=429, msg="Rate limit exceeded",
                    hdrs={}, fp=io.BytesIO(json.dumps({"error": {"type": "rate_limit"}}).encode()),
                )

        monkeypatch.setattr("bookcast.adapters.compatible.request.build_opener", lambda *args: FailingOpener())

        telemetry_file = tmp_path / "usage" / "physical_requests.jsonl"
        context = ProviderRequestContext(
            job_id="job-llm-2", output_id="out-llm-2",
            logical_chunk_id="dialogue:0001", provider=spec.name, model=spec.model,
            telemetry_path=telemetry_file, physical_attempt_index=0,
        )

        provider = CompatibleLLMProvider(spec)
        provider.set_context(context)
        with pytest.raises(ProviderError) as exc:
            provider.generate("write dialogue")
        assert exc.value.kind == ErrorKind.RATE_LIMIT

        assert telemetry_file.exists()
        lines = [json.loads(line) for line in telemetry_file.read_text().splitlines() if line.strip()]
        assert len(lines) == 1
        rec = lines[0]

        assert rec["provider"] == "qwen"
        assert rec["model"] == "qwen3.7-flash"
        assert rec["endpoint"] == "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
        assert rec["logical_chunk_id"] == "dialogue:0001"
        assert rec["physical_attempt_index"] == 0
        assert rec["http_status"] == 429
        assert rec["result"] == "failed"
        assert rec["retry_reason"] == "rate_limit"
        assert rec["retryable"] is True
        assert rec["usage_available"] is False
        assert rec["error_kind"] == "rate_limit"

    def test_chain_execution_propagates_attempt_index_and_prevents_double_counting(self, monkeypatch, tmp_path):
        """Chain execution threads context and retry physical_attempt_index while cost logic avoids double-counting."""
        from pydantic import BaseModel
        from bookcast.adapters.compatible import CompatibleLLMProvider
        from bookcast.provider_chain import ProviderChain
        from bookcast.provider_config import ProviderSpec
        spec = ProviderSpec(
            name="deepseek", kind="llm", type="openai-compatible",
            model="deepseek-flash", base_url="https://api.deepseek.com",
            api_key_env="DEEPSEEK_API_KEY",
        )
        monkeypatch.setenv("DEEPSEEK_API_KEY", "secret-test-key")

        class SchemaModel(BaseModel):
            message: str

        call_count = 0
        def fake_open(req, timeout):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # First attempt: incomplete response (finish_reason='length') triggering prepare_schema_retry
                body = {
                    "choices": [{"message": {"content": '{"message": "trun'}, "finish_reason": "length"}],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
                }
                return io.BytesIO(json.dumps(body).encode("utf-8"))
            # Second attempt: completed response
            body = {
                "choices": [{"message": {"content": '{"message": "full message"}'}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1000, "completion_tokens": 1000, "total_tokens": 2000, "prompt_cache_hit_tokens": 0}
            }
            return io.BytesIO(json.dumps(body).encode("utf-8"))

        class FakeOpener:
            def open(self, req, timeout):
                return fake_open(req, timeout)

        monkeypatch.setattr("bookcast.adapters.compatible.request.build_opener", lambda *args: FakeOpener())

        telemetry_file = tmp_path / "usage" / "physical_requests.jsonl"
        context = ProviderRequestContext(
            job_id="job-chain-1", output_id="out-chain-1",
            logical_chunk_id="test_step", provider=spec.name, model=spec.model,
            telemetry_path=telemetry_file, physical_attempt_index=0,
        )

        provider = CompatibleLLMProvider(spec)
        chain = ProviderChain([provider])

        observed_attempts = {}
        def observe(att):
            observed_attempts[att.id] = att.model_copy(deep=True)

        out_file = tmp_path / "out.json"
        artifacts = chain.execute(
            task="test_step", kind="llm", prompt_version="v1", input_hash="a" * 64,
            invoke=lambda p: [
                str(out_file.write_text(p.generate_structured("prompt", SchemaModel).model_dump_json(), encoding="utf-8"))
                and "out.json"
            ],
            persist=lambda names: {n: "hash123" for n in names},
            observe=observe,
            context=context,
        )

        assert artifacts == {"out.json": "hash123"}
        assert telemetry_file.exists()
        lines = [json.loads(line) for line in telemetry_file.read_text().splitlines() if line.strip()]
        # Two physical HTTP requests occurred: attempt 0 then attempt 1
        assert len(lines) == 2
        assert lines[0]["physical_attempt_index"] == 0
        assert lines[1]["physical_attempt_index"] == 1
        assert lines[0]["http_status"] == 200
        assert lines[1]["http_status"] == 200

        # Cost accounting must calculate from observed AIAttempts, completely unaffected by physical_requests.jsonl:
        config = ProvidersConfig.model_validate({
            "providers": [
                spec.model_dump(),
                {"name": "mock-tts", "kind": "tts", "type": "mock", "model": "mock-tts-v1"},
            ],
            "llm_priority": ["deepseek"],
            "tts_priority": ["mock-tts"],
            "pricing": {"deepseek-flash": {"default": {"uncached_input_per_million": 1.0, "output_per_million": 4.0}}}
        })
        completed_calls = [a for a in observed_attempts.values() if a.status == "completed"]
        summary = calculate_cost_summary(completed_calls, config)
        # Exactly 1 completed logical call accounted, no double counting
        assert summary["llm"]["providers"]["deepseek::deepseek-flash"]["requests"] == 1
        assert summary["total"]["known_amount"] > 0

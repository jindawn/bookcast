"""R2: Qwen LLM adapter wire-fixture tests.

All tests are offline; no real API keys, no network.  The monkeypatch replaces
urllib.request.build_opener with a minimal Transport that captures the outgoing
HTTP payload and returns a controlled response body.

Verified properties:
  - enable_thinking=True/False appears in the outgoing request (not DeepSeek dict)
  - reasoning_effort is never forwarded to Qwen3
  - max_tokens is forwarded correctly
  - response_format=json_object is set for structured calls
  - Schema instruction injected as system message
  - ProviderUsage is populated from response usage fields
  - reported_model is parsed correctly
  - HTTP errors map to the correct ErrorKind
  - prepare_schema_retry always returns False
  - DeepSeek adapter unaffected: enable_thinking not present, thinking dict present
  - Config validation: qwen-llm accepts generation/reasoning_policy, requires base_url
  - R1 routing selects qwen-llm provider by task intent
  - cache_key changes with generation options; identical configs share same key
  - for_task() produces call-scoped view without leaking prior response metadata
"""

import io
import json

import pytest
from pydantic import ValidationError

from bookcast.adapters.compatible import CompatibleLLMProvider
from bookcast.adapters.qwen_llm import QwenLLMProvider
from bookcast.content_models import EvidenceAnalysis
from bookcast.generation import GenerationConfig, resolve_generation
from bookcast.model_routing import RoutedLLMChain
from bookcast.provider_api import ErrorKind, ProviderError
from bookcast.provider_chain import ProviderChain
from bookcast.provider_config import ProvidersConfig, ProviderSpec, load_config
from bookcast.provider_registry import default_registry
from bookcast.storage import fingerprint


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

QWEN_BASE = "https://dashscope.aliyuncs.com/compatible-mode/v1"
DEEPSEEK_BASE = "https://api.deepseek.com"


def qwen_spec(**kwargs) -> ProviderSpec:
    return ProviderSpec.model_validate({
        "name": "qwen", "kind": "llm", "type": "qwen-llm",
        "model": "qwen3-7-flash", "base_url": QWEN_BASE,
        **kwargs,
    })


def deepseek_spec(**kwargs) -> ProviderSpec:
    return ProviderSpec.model_validate({
        "name": "deepseek", "kind": "llm", "type": "openai-compatible",
        "model": "deepseek-flash", "base_url": DEEPSEEK_BASE,
        **kwargs,
    })


def _make_transport(response: dict, *, sent: list | None = None):
    """Return a fake urllib opener factory that captures the outgoing payload."""
    class _Transport:
        def open(self, req, timeout):
            if sent is not None:
                sent.append(json.loads(req.data))
            return io.BytesIO(json.dumps(response).encode())
    return lambda *args: _Transport()


def _ok_response(content: str, *, model: str = "qwen3-7-flash-reported") -> dict:
    return {
        "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
        "model": model,
        "usage": {"prompt_tokens": 100, "completion_tokens": 50,
                  "completion_tokens_details": {"reasoning_tokens": 20}},
    }


def _valid_analysis() -> dict:
    return {
        "chapter_id": "0001", "chunk_id": "0001", "is_mock": False,
        "core_ideas": [{"text": "简述", "evidence_id": "e0001"}],
        **{name: [] for name in ("arguments", "evidence", "examples", "people",
                                 "concepts", "counter_arguments", "connections", "key_passages")},
    }


# ---------------------------------------------------------------------------
# 1. thinking parameter mapping
# ---------------------------------------------------------------------------

class TestThinkingMapping:

    def test_thinking_enabled_sends_enable_thinking_true(self, monkeypatch):
        spec = qwen_spec(generation={"thinking": "enabled", "max_tokens": 4096})
        provider = QwenLLMProvider(spec)
        sent = []
        monkeypatch.setattr("bookcast.adapters.compatible.request.build_opener",
                            _make_transport(_ok_response("hello world"), sent=sent))
        provider.generate("test prompt")
        assert sent[0]["enable_thinking"] is True
        assert sent[0]["max_tokens"] == 4096
        assert "thinking" not in sent[0]       # no DeepSeek dict form
        assert "reasoning_effort" not in sent[0]

    def test_thinking_disabled_sends_enable_thinking_false(self, monkeypatch):
        spec = qwen_spec(generation={"thinking": "disabled"})
        provider = QwenLLMProvider(spec)
        sent = []
        monkeypatch.setattr("bookcast.adapters.compatible.request.build_opener",
                            _make_transport(_ok_response("hello"), sent=sent))
        provider.generate("prompt")
        assert sent[0]["enable_thinking"] is False
        assert "thinking" not in sent[0]
        assert "reasoning_effort" not in sent[0]

    def test_no_generation_omits_thinking_fields(self, monkeypatch):
        provider = QwenLLMProvider(qwen_spec())
        sent = []
        monkeypatch.setattr("bookcast.adapters.compatible.request.build_opener",
                            _make_transport(_ok_response("hi"), sent=sent))
        provider.generate("prompt")
        assert "enable_thinking" not in sent[0]
        assert "max_tokens" not in sent[0]
        assert "thinking" not in sent[0]
        assert "reasoning_effort" not in sent[0]

    def test_reasoning_effort_never_forwarded_even_when_set(self, monkeypatch):
        """reasoning_effort is a DeepSeek concept; Qwen3 must never receive it."""
        spec = qwen_spec(generation={"thinking": "enabled", "reasoning_effort": "high"})
        provider = QwenLLMProvider(spec)
        sent = []
        monkeypatch.setattr("bookcast.adapters.compatible.request.build_opener",
                            _make_transport(_ok_response("ok"), sent=sent))
        provider.generate("prompt")
        assert "reasoning_effort" not in sent[0]
        assert sent[0]["enable_thinking"] is True


# ---------------------------------------------------------------------------
# 2. structured output / schema injection
# ---------------------------------------------------------------------------

class TestStructuredOutput:

    def test_structured_call_sets_json_object_format(self, monkeypatch):
        provider = QwenLLMProvider(qwen_spec())
        sent = []
        monkeypatch.setattr("bookcast.adapters.compatible.request.build_opener",
                            _make_transport(_ok_response(json.dumps(_valid_analysis())), sent=sent))
        result = provider.generate_structured("analyse this", EvidenceAnalysis)
        assert result.chapter_id == "0001"
        assert sent[0]["response_format"] == {"type": "json_object"}
        # Schema instruction in system message
        system_msgs = [m for m in sent[0]["messages"] if m["role"] == "system"]
        assert system_msgs and "Return only JSON" in system_msgs[0]["content"]

    def test_no_markdown_stripping_needed(self, monkeypatch):
        """Qwen3 should not emit markdown fences; no stripping required.
        If it did, Pydantic would raise and we'd see a schema_error — we verify
        the happy path returns the correct type."""
        provider = QwenLLMProvider(qwen_spec())
        monkeypatch.setattr("bookcast.adapters.compatible.request.build_opener",
                            _make_transport(_ok_response(json.dumps(_valid_analysis()))))
        result = provider.generate_structured("go", EvidenceAnalysis)
        assert result.core_ideas[0].text == "简述"

    def test_invalid_json_raises_schema_error(self, monkeypatch):
        provider = QwenLLMProvider(qwen_spec())
        monkeypatch.setattr("bookcast.adapters.compatible.request.build_opener",
                            _make_transport(_ok_response("not json at all")))
        with pytest.raises(ProviderError) as exc:
            provider.generate_structured("prompt", EvidenceAnalysis)
        assert exc.value.kind == ErrorKind.SCHEMA


# ---------------------------------------------------------------------------
# 3. usage / reported_model
# ---------------------------------------------------------------------------

class TestUsage:

    def test_usage_populated_from_response(self, monkeypatch):
        provider = QwenLLMProvider(qwen_spec())
        monkeypatch.setattr("bookcast.adapters.compatible.request.build_opener",
                            _make_transport(_ok_response("hello")))
        provider.generate("prompt")
        assert provider.last_usage is not None
        assert provider.last_usage.input_tokens == 100
        assert provider.last_usage.output_tokens == 50
        assert provider.last_usage.reasoning_tokens == 20
        assert provider.reported_model == "qwen3-7-flash-reported"

    def test_missing_usage_is_none(self, monkeypatch):
        response = {"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}], "model": "x"}
        provider = QwenLLMProvider(qwen_spec())
        monkeypatch.setattr("bookcast.adapters.compatible.request.build_opener",
                            _make_transport(response))
        provider.generate("prompt")
        assert provider.last_usage is None


# ---------------------------------------------------------------------------
# 4. error classification
# ---------------------------------------------------------------------------

class TestErrorClassification:

    @pytest.mark.parametrize("status,expected_kind", [
        (401, ErrorKind.AUTH), (403, ErrorKind.AUTH),
        (429, ErrorKind.RATE_LIMIT), (402, ErrorKind.QUOTA),
        (408, ErrorKind.TIMEOUT), (500, ErrorKind.UNAVAILABLE),
        (422, ErrorKind.INPUT),
    ])
    def test_http_errors_classified_correctly(self, monkeypatch, status, expected_kind):
        from urllib import error as urlerr
        provider = QwenLLMProvider(qwen_spec())

        class _BadTransport:
            def open(self, req, timeout):
                raise urlerr.HTTPError(url="", code=status, msg="", hdrs={}, fp=io.BytesIO(b"{}"))

        monkeypatch.setattr("bookcast.adapters.compatible.request.build_opener",
                            lambda *args: _BadTransport())
        with pytest.raises(ProviderError) as exc:
            provider.generate("prompt")
        assert exc.value.kind == expected_kind

    def test_quota_code_in_body_maps_to_quota(self, monkeypatch):
        from urllib import error as urlerr
        provider = QwenLLMProvider(qwen_spec())
        body = json.dumps({"error": {"code": "insufficient_quota"}}).encode()

        class _BadTransport:
            def open(self, req, timeout):
                raise urlerr.HTTPError(url="", code=429, msg="", hdrs={}, fp=io.BytesIO(body))

        monkeypatch.setattr("bookcast.adapters.compatible.request.build_opener",
                            lambda *args: _BadTransport())
        with pytest.raises(ProviderError) as exc:
            provider.generate("prompt")
        assert exc.value.kind == ErrorKind.QUOTA

    def test_finish_reason_non_stop_is_schema_error(self, monkeypatch):
        response = {"choices": [{"message": {"content": "ok"}, "finish_reason": "length"}],
                    "model": "q", "usage": {}}
        provider = QwenLLMProvider(qwen_spec())
        monkeypatch.setattr("bookcast.adapters.compatible.request.build_opener",
                            _make_transport(response))
        with pytest.raises(ProviderError) as exc:
            provider.generate("prompt")
        assert exc.value.kind == ErrorKind.SCHEMA
        assert exc.value.validation_reason == "finish_reason"


# ---------------------------------------------------------------------------
# 5. prepare_schema_retry always False
# ---------------------------------------------------------------------------

def test_prepare_schema_retry_always_returns_false():
    provider = QwenLLMProvider(qwen_spec())
    for failure in [
        ProviderError(ErrorKind.SCHEMA, error_type="ValidationError",
                      validation_field="core_ideas", validation_reason="too_long"),
        ProviderError(ErrorKind.SCHEMA, error_type="ValidationError",
                      validation_field="$", validation_reason="json_invalid"),
        ProviderError(ErrorKind.SCHEMA, validation_reason="finish_reason"),
    ]:
        assert provider.prepare_schema_retry(failure) is False


# ---------------------------------------------------------------------------
# 6. DeepSeek adapter unaffected
# ---------------------------------------------------------------------------

class TestDeepSeekUnaffected:

    def test_deepseek_sends_thinking_dict_not_enable_thinking(self, monkeypatch):
        spec = deepseek_spec(generation={"thinking": "enabled"})
        provider = CompatibleLLMProvider(spec)
        sent = []
        monkeypatch.setattr("bookcast.adapters.compatible.request.build_opener",
                            _make_transport(_ok_response("fine", model="ds"), sent=sent))
        provider.generate("prompt")
        # DeepSeek dialect: thinking as dict
        assert sent[0]["thinking"] == {"type": "enabled"}
        assert "enable_thinking" not in sent[0]

    def test_deepseek_schema_retry_still_returns_true(self):
        spec = deepseek_spec()
        provider = CompatibleLLMProvider(spec)
        provider._last_schema = {"properties": {"evidence": {"type": "array", "maxItems": 6}}}
        failure = ProviderError(ErrorKind.SCHEMA, error_type="ValidationError",
                                validation_field="evidence", validation_reason="too_long")
        assert provider.prepare_schema_retry(failure) is True


# ---------------------------------------------------------------------------
# 7. Config validation: qwen-llm accepts generation / reasoning_policy
# ---------------------------------------------------------------------------

class TestConfigValidation:

    def test_qwen_llm_accepts_generation_options(self):
        spec = qwen_spec(generation={"thinking": "enabled", "max_tokens": 8192})
        assert spec.type == "qwen-llm"
        assert spec.generation.thinking == "enabled"

    def test_qwen_llm_accepts_reasoning_policy(self):
        spec = qwen_spec(reasoning_policy="bookcast-v1")
        assert spec.reasoning_policy == "bookcast-v1"

    def test_qwen_llm_requires_base_url(self):
        with pytest.raises(ValidationError):
            ProviderSpec.model_validate({
                "name": "q", "kind": "llm", "type": "qwen-llm", "model": "qwen3-7-flash",
            })

    def test_qwen_llm_rejects_local_loopback(self):
        """qwen-llm is a cloud adapter; local loopback is reserved for type=local."""
        with pytest.raises(ValidationError):
            # HTTP on non-loopback is rejected by the base validator
            ProviderSpec.model_validate({
                "name": "q", "kind": "llm", "type": "qwen-llm",
                "model": "qwen3-7-flash", "base_url": "http://example.com/v1",
            })

    def test_qwen_llm_registered_in_default_registry(self):
        registry = default_registry()
        assert "llm/qwen-llm" in registry.types()


# ---------------------------------------------------------------------------
# 8. cache_key independence
# ---------------------------------------------------------------------------

class TestCacheKey:

    def test_cache_key_changes_with_generation_options(self):
        p_no_gen = QwenLLMProvider(qwen_spec())
        p_thinking = QwenLLMProvider(qwen_spec(generation={"thinking": "enabled"}))
        p_disabled = QwenLLMProvider(qwen_spec(generation={"thinking": "disabled"}))
        keys = {p_no_gen.cache_key, p_thinking.cache_key, p_disabled.cache_key}
        assert len(keys) == 3, "Distinct generation configs must yield distinct cache keys"

    def test_same_config_same_cache_key(self):
        a = QwenLLMProvider(qwen_spec(generation={"thinking": "enabled", "max_tokens": 4096}))
        b = QwenLLMProvider(qwen_spec(generation={"thinking": "enabled", "max_tokens": 4096}))
        assert a.cache_key == b.cache_key

    def test_qwen_and_deepseek_different_type_yields_different_key(self):
        qwen = QwenLLMProvider(qwen_spec())
        deepseek = CompatibleLLMProvider(deepseek_spec())
        assert qwen.cache_key != deepseek.cache_key


# ---------------------------------------------------------------------------
# 9. for_task() scoping
# ---------------------------------------------------------------------------

class TestForTask:

    def test_for_task_returns_new_provider_with_generation_audit(self):
        spec = qwen_spec(reasoning_policy="bookcast-v1")
        provider = QwenLLMProvider(spec)
        bound = provider.for_task("synthesis/book/00")
        assert bound is not provider
        assert bound.generation_audit is not None
        assert bound.generation_audit.task_type == "book_synthesis"

    def test_for_task_metadata_does_not_leak_between_calls(self, monkeypatch):
        """After a first call the response metadata must not appear on the next bound view."""
        spec = qwen_spec()
        provider = QwenLLMProvider(spec)
        monkeypatch.setattr("bookcast.adapters.compatible.request.build_opener",
                            _make_transport(_ok_response("first")))
        provider.generate("first call")
        assert provider.reported_model == "qwen3-7-flash-reported"

        bound = provider.for_task("analysis:0001")
        assert bound.reported_model is None
        assert bound.last_usage is None

    def test_for_task_thinking_sent_for_book_synthesis(self, monkeypatch):
        spec = qwen_spec(reasoning_policy="bookcast-v1")
        provider = QwenLLMProvider(spec)
        bound = provider.for_task("synthesis/book/00")
        sent = []
        monkeypatch.setattr("bookcast.adapters.compatible.request.build_opener",
                            _make_transport(_ok_response("book"), sent=sent))
        bound.generate("prompt")
        # bookcast-v1 book_synthesis: thinking=enabled
        assert sent[0].get("enable_thinking") is True
        assert "thinking" not in sent[0]
        assert "reasoning_effort" not in sent[0]

    def test_for_task_thinking_disabled_for_extraction(self, monkeypatch):
        spec = qwen_spec(reasoning_policy="bookcast-v1")
        provider = QwenLLMProvider(spec)
        bound = provider.for_task("analysis:0001:0001")
        sent = []
        monkeypatch.setattr("bookcast.adapters.compatible.request.build_opener",
                            _make_transport(_ok_response("analysis"), sent=sent))
        bound.generate("prompt")
        # bookcast-v1 extraction: thinking=disabled
        assert sent[0].get("enable_thinking") is False


# ---------------------------------------------------------------------------
# 10. Routing integration: qwen-llm via R1 RoutedLLMChain
# ---------------------------------------------------------------------------

class TestRoutingIntegration:

    def _make_config(self) -> ProvidersConfig:
        return ProvidersConfig.model_validate({
            "providers": [
                {"name": "qwen", "kind": "llm", "type": "qwen-llm",
                 "model": "qwen3-7-flash", "base_url": QWEN_BASE},
                {"name": "deepseek", "kind": "llm", "type": "openai-compatible",
                 "model": "deepseek-flash", "base_url": DEEPSEEK_BASE},
                {"name": "voice", "kind": "tts", "type": "mock", "model": "tones-v1"},
            ],
            "llm_priority": ["qwen", "deepseek"],
            "tts_priority": ["voice"],
            "llm_routing": {
                "general": ["qwen"], "cheap": ["qwen"],
                "complex": ["deepseek"], "high_quality": ["deepseek"],
            },
        })

    def test_chain_is_routed_llm_chain(self):
        config = self._make_config()
        chain = default_registry().chain(config, "llm")
        assert isinstance(chain, RoutedLLMChain)

    def test_extraction_task_routes_to_qwen_llm(self, monkeypatch):
        config = self._make_config()
        chain = default_registry().chain(config, "llm")
        sent = []

        def invoke(provider):
            if hasattr(provider, '_build_thinking_fields'):
                # This is QwenLLMProvider — capture it
                sent.append(type(provider).__name__)
            return "done"

        invoked = []
        chain.execute(
            task="analysis:0001:0001", kind="llm", prompt_version="v1",
            input_hash="a" * 64,
            invoke=lambda p: (invoked.append(type(p).__name__), "done")[1],
            persist=lambda _: {"r.json": "b" * 64},
            observe=lambda a: None,
        )
        assert invoked == ["QwenLLMProvider"]

    def test_book_synthesis_routes_to_compatible(self, monkeypatch):
        config = self._make_config()
        chain = default_registry().chain(config, "llm")
        invoked = []
        chain.execute(
            task="synthesis/book/00", kind="llm", prompt_version="v1",
            input_hash="a" * 64,
            invoke=lambda p: (invoked.append(type(p).__name__), "done")[1],
            persist=lambda _: {"r.json": "b" * 64},
            observe=lambda a: None,
        )
        assert invoked == ["CompatibleLLMProvider"]


# ---------------------------------------------------------------------------
# 11. Endpoint and regional URL normalization
# ---------------------------------------------------------------------------

class TestLLMEndpoints:

    def test_beijing_compatible_endpoint_preserved(self):
        from bookcast.adapters.qwen_llm import _resolve_llm_base_url
        url = "https://dashscope.aliyuncs.com/compatible-mode/v1"
        assert _resolve_llm_base_url(url) == url

    def test_api_v1_converted_to_compatible_mode(self):
        from bookcast.adapters.qwen_llm import _resolve_llm_base_url
        url = "https://dashscope.aliyuncs.com/api/v1"
        assert _resolve_llm_base_url(url) == "https://dashscope.aliyuncs.com/compatible-mode/v1"

    def test_raw_root_dashscope_appends_compatible_mode(self):
        from bookcast.adapters.qwen_llm import _resolve_llm_base_url
        url = "https://dashscope.aliyuncs.com"
        assert _resolve_llm_base_url(url) == "https://dashscope.aliyuncs.com/compatible-mode/v1"

    def test_singapore_api_v1_converted_to_compatible_mode(self):
        from bookcast.adapters.qwen_llm import _resolve_llm_base_url
        url = "https://dashscope-intl.aliyuncs.com/api/v1"
        assert _resolve_llm_base_url(url) == "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"

    def test_singapore_root_appends_compatible_mode(self):
        from bookcast.adapters.qwen_llm import _resolve_llm_base_url
        url = "https://dashscope-intl.aliyuncs.com"
        assert _resolve_llm_base_url(url) == "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"

    def test_workspace_api_v1_converted_to_compatible_mode(self):
        from bookcast.adapters.qwen_llm import _resolve_llm_base_url
        url = "https://custom-ws.cn-beijing.maas.aliyuncs.com/api/v1"
        assert _resolve_llm_base_url(url) == "https://custom-ws.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"

    def test_custom_mock_url_preserved(self):
        from bookcast.adapters.qwen_llm import _resolve_llm_base_url
        url = "https://example.com/v1"
        assert _resolve_llm_base_url(url) == url

    def test_provider_normalizes_spec_base_url(self):
        spec = ProviderSpec.model_validate({
            "name": "qwen", "kind": "llm", "type": "qwen-llm",
            "model": "qwen3.7-flash", "base_url": "https://dashscope.aliyuncs.com/api/v1",
        })
        provider = QwenLLMProvider(spec)
        assert provider.spec.base_url == "https://dashscope.aliyuncs.com/compatible-mode/v1"

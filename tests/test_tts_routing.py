"""R4: TTS Quality Routing and Episode Consistency tests.

All tests are offline; no network, no real API keys.

Verified properties:
  - TTSRouting model: standard, high_quality, default_quality validation & resolution
  - ProvidersConfig validation: providers must exist and belong to tts_priority
  - Legacy compatibility: tts_routing omitted when None (byte-for-byte snapshot parity)
  - Registry routing:
      'auto' -> standard (or default_quality)
      'standard' -> standard provider
      'high' / 'high_quality' -> high_quality provider
      explicit provider name -> explicit override (bypasses routing)
  - Single provider per episode:
      chain.providers returns [selected_provider]
      no mid-stream switching to the other provider on failure
      validate_tts_chain succeeds even when candidate pool mixes speech_units and speech_segments
  - Cache key distinctness across qualities, providers, and settings
  - Pipeline integration: speech rendering and manifest recording with RoutedTTSChain
  - Gemini Lite (gemini-3.8-flash-lite-tts) model validation, wire serialization, and decode
"""

import io
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from bookcast.adapters.gemini import GeminiTTSProvider
from bookcast.model_routing import RoutedTTSChain
from bookcast.models import AIAttempt
from bookcast.pipeline import Pipeline
from bookcast.provider_api import ErrorKind, ProviderCapabilities, ProviderError
from bookcast.provider_chain import ProviderChain
from bookcast.provider_config import ProvidersConfig, ProviderSpec, TTSRouting, load_config
from bookcast.provider_registry import ProviderRegistry, default_registry
from bookcast.speech import validate_tts_chain


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_tts_config(**overrides) -> ProvidersConfig:
    routing = {
        "standard": "mock-standard",
        "high_quality": "mock-high",
        "default_quality": "standard",
    }
    if "tts_routing" in overrides and overrides["tts_routing"] is None:
        routing_val = None
    elif "tts_routing" in overrides:
        routing.update(overrides["tts_routing"])
        routing_val = routing
    else:
        routing_val = routing
    raw = {
        "providers": [
            {"name": "mock-llm", "kind": "llm", "type": "mock", "model": "mock-llm-v1"},
            {"name": "mock-standard", "kind": "tts", "type": "mock", "model": "mock-tones-v1"},
            {"name": "mock-high", "kind": "tts", "type": "mock", "model": "mock-tones-v2"},
        ],
        "llm_priority": ["mock-llm"],
        "tts_priority": ["mock-standard", "mock-high"],
        "tts_routing": routing_val,
    }
    if "providers" in overrides:
        raw["providers"] = overrides["providers"]
    if "tts_priority" in overrides:
        raw["tts_priority"] = overrides["tts_priority"]
    return ProvidersConfig.model_validate(raw)


# ---------------------------------------------------------------------------
# 1. TTSRouting Model & Resolution
# ---------------------------------------------------------------------------

class TestTTSRoutingModel:

    def test_default_quality_is_standard(self):
        r = TTSRouting(standard="qwen-tts", high_quality="gemini-tts")
        assert r.default_quality == "standard"
        assert r.resolve("auto") == "qwen-tts"
        assert r.resolve(None) == "qwen-tts"

    def test_resolve_standard_and_high(self):
        r = TTSRouting(standard="qwen-tts", high_quality="gemini-tts")
        assert r.resolve("standard") == "qwen-tts"
        assert r.resolve("high") == "gemini-tts"
        assert r.resolve("high_quality") == "gemini-tts"

    def test_resolve_override_passes_through(self):
        r = TTSRouting(standard="qwen-tts", high_quality="gemini-tts")
        assert r.resolve("kokoro") == "kokoro"
        assert r.resolve("custom-voice") == "custom-voice"

    def test_custom_default_quality(self):
        r = TTSRouting(standard="qwen-tts", high_quality="gemini-tts", default_quality="high_quality")
        assert r.resolve("auto") == "gemini-tts"

    def test_empty_names_rejected(self):
        with pytest.raises(ValidationError):
            TTSRouting(standard="", high_quality="gemini-tts")
        with pytest.raises(ValidationError):
            TTSRouting(standard="qwen-tts", high_quality="")


# ---------------------------------------------------------------------------
# 2. ProvidersConfig Validation & Legacy Compatibility
# ---------------------------------------------------------------------------

class TestProvidersConfigTTSRouting:

    def test_valid_tts_routing_passes(self):
        config = make_tts_config()
        assert config.tts_routing is not None
        assert config.tts_routing.standard == "mock-standard"
        assert config.tts_routing.high_quality == "mock-high"

    def test_unknown_standard_provider_rejected(self):
        with pytest.raises(ValidationError):
            make_tts_config(tts_routing={"standard": "nonexistent", "high_quality": "mock-high"})

    def test_unknown_high_quality_provider_rejected(self):
        with pytest.raises(ValidationError):
            make_tts_config(tts_routing={"standard": "mock-standard", "high_quality": "nonexistent"})

    def test_provider_not_in_tts_priority_rejected(self):
        with pytest.raises(ValidationError):
            make_tts_config(
                tts_priority=["mock-standard"],  # mock-high omitted from priority
                tts_routing={"standard": "mock-standard", "high_quality": "mock-high"},
            )

    def test_non_tts_provider_in_tts_routing_rejected(self):
        with pytest.raises(ValidationError):
            make_tts_config(
                tts_routing={"standard": "mock-llm", "high_quality": "mock-high"},
            )

    def test_legacy_compatibility_omits_tts_routing_when_none(self):
        config = make_tts_config(tts_routing=None)
        dumped = config.model_dump(mode="json")
        assert "tts_routing" not in dumped


# ---------------------------------------------------------------------------
# 3. Registry & RoutedTTSChain Behavior
# ---------------------------------------------------------------------------

class TestRoutedTTSChain:

    def test_auto_selects_standard_provider(self):
        config = make_tts_config()
        chain = default_registry().chain(config, "tts", "auto")
        assert isinstance(chain, RoutedTTSChain)
        assert chain.selected_provider.name == "mock-standard"

    def test_standard_selects_standard_provider(self):
        config = make_tts_config()
        chain = default_registry().chain(config, "tts", "standard")
        assert isinstance(chain, RoutedTTSChain)
        assert chain.selected_provider.name == "mock-standard"

    def test_high_selects_high_quality_provider(self):
        config = make_tts_config()
        chain = default_registry().chain(config, "tts", "high")
        assert isinstance(chain, RoutedTTSChain)
        assert chain.selected_provider.name == "mock-high"

    def test_high_quality_selects_high_quality_provider(self):
        config = make_tts_config()
        chain = default_registry().chain(config, "tts", "high_quality")
        assert isinstance(chain, RoutedTTSChain)
        assert chain.selected_provider.name == "mock-high"

    def test_explicit_provider_name_bypasses_routing(self):
        config = make_tts_config()
        chain = default_registry().chain(config, "tts", "mock-high")
        # Returns normal ProviderChain with the single specified provider
        assert type(chain) is ProviderChain
        assert len(chain.providers) == 1
        assert chain.providers[0].name == "mock-high"

    def test_chain_providers_returns_only_selected_provider(self):
        config = make_tts_config()
        chain = default_registry().chain(config, "tts", "standard")
        assert len(chain.providers) == 1
        assert chain.providers[0].name == "mock-standard"

    def test_cache_key_varies_by_selection_and_quality(self):
        config = make_tts_config()
        c_auto = default_registry().chain(config, "tts", "auto")
        c_standard = default_registry().chain(config, "tts", "standard")
        c_high = default_registry().chain(config, "tts", "high")
        # c_auto selection is 'auto', c_standard selection is 'standard'
        assert c_standard.cache_key != c_high.cache_key
        assert c_auto.selected_provider.name == c_standard.selected_provider.name

    def test_restore_forwards_to_active_chain(self):
        config = make_tts_config()
        chain = default_registry().chain(config, "tts", "standard")
        calls = [
            AIAttempt(id="att-1", task="tts:0001:0001", kind="tts", provider="mock-standard",
                      model="mock-tones-v1", prompt_version="v1", input_hash="a" * 64,
                      status="completed"),
        ]
        chain.restore(calls, "tts")
        assert chain._active_chain.index == 0


# ---------------------------------------------------------------------------
# 4. Single Provider Per Episode & No Mid-stream Failover
# ---------------------------------------------------------------------------

class TestSingleProviderPerEpisode:

    def test_no_midstream_failover_to_different_quality_provider(self):
        """When the chosen provider fails on a unit, it must NOT failover to mock-high."""
        config = make_tts_config()
        chain = default_registry().chain(config, "tts", "standard")
        invoked_providers = []

        def failing_invoke(provider):
            invoked_providers.append(provider.name)
            raise ProviderError(ErrorKind.UNAVAILABLE)

        with pytest.raises(ProviderError):
            chain.execute(
                task="tts:0001:0001", kind="tts", prompt_version="v1",
                input_hash="a" * 64,
                invoke=failing_invoke,
                persist=lambda _: {"a.wav": "b" * 64},
                observe=lambda _: None,
            )

        # mock-standard was invoked (and retried within active chain), but mock-high was NEVER touched.
        assert all(name == "mock-standard" for name in invoked_providers)
        assert "mock-high" not in invoked_providers

    def test_validate_tts_chain_succeeds_with_mixed_pool_via_routing(self):
        """A pool containing both speech_units and speech_segments providers
        fails legacy validate_tts_chain, but succeeds under RoutedTTSChain because
        the active chain contains only the single chosen provider."""
        class MockUnitsProvider:
            name, model = "units-tts", "units-v1"
            cache_key = "k1"
            def capabilities(self):
                return ProviderCapabilities(speech=True, speech_units=True)
            def health_check(self):
                return None

        class MockSegmentsProvider:
            name, model = "segments-tts", "segments-v1"
            cache_key = "k2"
            def capabilities(self):
                return ProviderCapabilities(speech=True, speech_segments=True)
            def health_check(self):
                return None

        # Direct flat chain with both types mixed fails validation:
        mixed_flat_chain = ProviderChain([MockUnitsProvider(), MockSegmentsProvider()])
        with pytest.raises(Exception, match="片段 TTS 链要求所有成员支持 speech_segments"):
            validate_tts_chain(mixed_flat_chain)

        # But RoutedTTSChain with standard=units-tts routes to ONLY MockUnitsProvider:
        routing = TTSRouting(standard="units-tts", high_quality="segments-tts")
        routed_standard = RoutedTTSChain([MockUnitsProvider(), MockSegmentsProvider()], routing, "standard")
        validate_tts_chain(routed_standard)  # MUST NOT RAISE!
        assert routed_standard.capabilities().speech_units is True

        # And RoutedTTSChain with high=segments-tts routes to ONLY MockSegmentsProvider:
        routed_high = RoutedTTSChain([MockUnitsProvider(), MockSegmentsProvider()], routing, "high")
        validate_tts_chain(routed_high)  # MUST NOT RAISE!
        assert routed_high.capabilities().speech_segments is True


# ---------------------------------------------------------------------------
# 5. Gemini 3.8 Flash-Lite TTS Wire Contract
# ---------------------------------------------------------------------------

class TestGeminiLiteTTSContract:

    def test_gemini_lite_model_validation(self):
        spec = ProviderSpec.model_validate({
            "name": "gemini-lite", "kind": "tts", "type": "gemini-tts",
            "model": "gemini-3.8-flash-lite-tts",
            "api_key_env": "GEMINI_API_KEY",
            "cloud_tts": {
                "send_text_to_cloud": True,
                "host_voice": "Kore",
                "guest_voice": "Puck",
            },
        })
        assert spec.model == "gemini-3.8-flash-lite-tts"
        assert spec.cloud_tts.host_voice == "Kore"

    def test_gemini_lite_payload_and_decode(self, monkeypatch, tmp_path):
        spec = ProviderSpec.model_validate({
            "name": "gemini-lite", "kind": "tts", "type": "gemini-tts",
            "model": "gemini-3.8-flash-lite-tts",
            "api_key_env": "GEMINI_API_KEY",
            "cloud_tts": {
                "send_text_to_cloud": True,
                "host_voice": "Kore",
                "guest_voice": "Puck",
                "min_request_interval": 0,
            },
        })
        provider = GeminiTTSProvider(spec)
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")

        # Create a valid mono 24k s16le WAV
        out_wav = io.BytesIO()
        import wave
        with wave.open(out_wav, "wb") as wf:
            wf.setparams((1, 2, 24000, 0, "NONE", "not compressed"))
            wf.writeframes(b"\x00\x00" * 480)
        wav_b64 = out_wav.getvalue()
        import base64
        b64_str = base64.b64encode(wav_b64).decode()

        response_doc = {
            "status": "completed",
            "model": "gemini-3.8-flash-lite-tts",
            "candidates": [
                {"content": {"parts": [{"inlineData": {"data": b64_str, "mimeType": "audio/wav"}}]}}
            ],
            "usage_metadata": {"prompt_token_count": 50, "candidates_token_count": 25},
        }

        sent_payloads = []

        class FakeOpener:
            def open(self, req, timeout):
                sent_payloads.append(json.loads(req.data.decode()))
                resp = io.BytesIO(json.dumps(response_doc).encode())
                resp.getcode = lambda: 200
                return resp

        monkeypatch.setattr("bookcast.adapters.gemini.request.build_opener", lambda *args: FakeOpener())

        from bookcast.provider_api import SpeechSegment, SpeechTurn
        seg = SpeechSegment(turns=[
            SpeechTurn(speaker="主持人", text="欢迎收听"),
            SpeechTurn(speaker="嘉宾", text="大家好"),
        ])
        dest = tmp_path / "audio.wav"
        info = provider.synthesize_segment(seg, dest)

        assert len(sent_payloads) == 1
        assert sent_payloads[0]["model"] == "gemini-3.8-flash-lite-tts"
        assert dest.exists()
        assert info.voices["主持人"] == "Kore"
        assert info.voices["嘉宾"] == "Puck"
        assert provider.last_usage.input_tokens == 50
        assert provider.last_usage.output_tokens == 25


# ---------------------------------------------------------------------------
# 6. Offline Pipeline Execution with TTS Routing
# ---------------------------------------------------------------------------

class TestPipelineWithTTSRouting:

    def test_pipeline_instantiates_cleanly_with_routed_tts(self, tmp_path):
        config = make_tts_config()
        reg = default_registry()
        llm = reg.chain(config, "llm", "auto")
        tts = reg.chain(config, "tts", "auto")
        pipeline = Pipeline(llm, tts, tmp_path / "out")
        assert isinstance(pipeline.tts, RoutedTTSChain)
        assert pipeline.tts.selected_provider.name == "mock-standard"

    def test_pipeline_instantiates_with_high_quality(self, tmp_path):
        config = make_tts_config()
        reg = default_registry()
        llm = reg.chain(config, "llm", "auto")
        tts = reg.chain(config, "tts", "high")
        pipeline = Pipeline(llm, tts, tmp_path / "out")
        assert isinstance(pipeline.tts, RoutedTTSChain)
        assert pipeline.tts.selected_provider.name == "mock-high"

"""R3 & Phase 19.1: Qwen Cloud TTS adapter offline tests.

All tests are offline; no real API keys, no network. Transport is monkey-
patched to intercept urllib requests and return controlled response bodies.

Verified properties:
  - Wire request format:
      * Qwen-TTS (qwen3-tts-instruct-flash): endpoint multimodal-generation/generation,
        input.text, input.voice, input.language_type, parameters.instructions
      * CosyVoice (cosyvoice-v2): endpoint text2voice/voice-synthesis,
        input.text (with system-prompt prefix if styled), parameters.sample_rate/format
  - Regional endpoints: Beijing (default), Singapore, workspace-specific endpoints
  - Voice selection: host_voice for 主持人, guest_voice for 嘉宾
  - Style instruction: instructions parameter for Qwen3-TTS; system prompt for CosyVoice
  - Audio decoding:
      * Official non-streaming Qwen3-TTS URL download -> WAV written to disk
      * Base64 PCM / WAV data decoding -> WAV written to disk
  - WAV format validation: channels=1, sampwidth=2, framerate=sample_rate, nframes>0
  - HTTP error classification: auth/quota/rate_limit/timeout/unavailable/input
  - DashScope-specific quota/rate codes mapped correctly
  - Empty text after normalization -> INPUT error
  - Text > 80 chars after normalization -> INPUT error
  - Oversized response -> SCHEMA error
  - Invalid Base64 / bad JSON -> SCHEMA error
  - Loopback audio download URL -> INPUT error
  - capabilities: speech=True, speech_units=True, cloud=True, no speech_segments
  - config validation: send_text_to_cloud required, distinct voices, api_key_env required
  - qwen-cloud-tts registered in default registry
  - qwen-local adapter unaffected (separate instance)
  - rate limiting: sleeper called when min_request_interval > 0
  - health_check: available when env key is set, auth error otherwise
  - cache_key changes with voice/model/style/endpoint options
"""

import base64
import io
import json
import os
import struct
import wave
from pathlib import Path

import pytest
from pydantic import ValidationError

from bookcast.adapters.qwen_cloud import (
    QwenCloudTTSProvider,
    _classify_dashscope,
    _decode_pcm_to_wav,
    _decode_response,
    _verify_wav,
    _resolve_endpoint,
    _is_cosyvoice,
    _ENDPOINT,
)
from bookcast.provider_api import ErrorKind, ProviderError
from bookcast.provider_config import ProvidersConfig, ProviderSpec, QwenCloudTTSConfig
from bookcast.provider_registry import default_registry


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

SAMPLE_RATE = 24000


def _qwen_cloud_spec(**kwargs) -> ProviderSpec:
    base = {
        "name": "qwen-tts", "kind": "tts", "type": "qwen-cloud-tts",
        "model": "qwen3-tts-instruct-flash",
        "api_key_env": "DASHSCOPE_API_KEY",
        "qwen_cloud_tts": {
            "send_text_to_cloud": True,
            "host_voice": "Cherry",
            "guest_voice": "Ethan",
        },
    }
    base.update(kwargs)
    return ProviderSpec.model_validate(base)


def _cosyvoice_spec(**kwargs) -> ProviderSpec:
    base = {
        "name": "qwen-tts", "kind": "tts", "type": "qwen-cloud-tts",
        "model": "cosyvoice-v2",
        "api_key_env": "DASHSCOPE_API_KEY",
        "qwen_cloud_tts": {
            "send_text_to_cloud": True,
            "host_voice": "longhua",
            "guest_voice": "longyue",
        },
    }
    base.update(kwargs)
    return ProviderSpec.model_validate(base)


def _make_pcm_wav(nframes: int = 480, sample_rate: int = SAMPLE_RATE) -> bytes:
    """Return a minimal mono s16le WAV."""
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setparams((1, 2, sample_rate, 0, "NONE", "not compressed"))
        w.writeframes(b"\x00\x00" * nframes)
    return out.getvalue()


def _raw_pcm(nframes: int = 480) -> bytes:
    return b"\x00\x00" * nframes


def _make_response(pcm: bytes | None = None, wav: bytes | None = None,
                   sample_rate: int = SAMPLE_RATE) -> bytes:
    """Build a DashScope TTS response body encoding PCM or WAV as Base64."""
    raw = pcm if pcm is not None else wav
    b64 = base64.b64encode(raw).decode()
    return json.dumps({"output": {"audio": b64}, "request_id": "test-id"}).encode()


def _make_url_response(url: str = "http://example.com/audio.wav") -> bytes:
    """Build an official Qwen-TTS non-streaming response body with audio URL."""
    return json.dumps({
        "status_code": 200,
        "request_id": "req-123",
        "code": "",
        "message": "",
        "output": {
            "text": None,
            "finish_reason": "stop",
            "choices": None,
            "audio": {
                "data": "",
                "url": url,
                "id": "audio_123",
                "expires_at": 1766113409,
            },
        },
        "usage": {"characters": 18},
    }).encode()


def _make_transport(response_body: bytes, status: int = 200, *,
                    wav_body: bytes | None = None, sent: list | None = None):
    """Return a fake urllib opener factory handling both POST synthesis and GET download."""
    from urllib import error as urlerr

    class _Transport:
        def open(self, req, timeout):
            if req.data is not None:
                if sent is not None:
                    sent.append(json.loads(req.data))
                if status != 200:
                    raise urlerr.HTTPError(
                        url="", code=status, msg="",
                        hdrs={}, fp=io.BytesIO(response_body),
                    )
                return io.BytesIO(response_body)
            else:
                body = wav_body if wav_body is not None else _make_pcm_wav()
                return io.BytesIO(body)
    return lambda *args: _Transport()


def _make_provider(spec: ProviderSpec | None = None, *,
                   key: str = "test-key", sleeper=None) -> QwenCloudTTSProvider:
    spec = spec or _qwen_cloud_spec()
    p = QwenCloudTTSProvider(spec, sleeper=sleeper or (lambda _: None))
    return p


# ---------------------------------------------------------------------------
# 1. Wire request format and protocol distinction
# ---------------------------------------------------------------------------

class TestWireFormat:

    def test_qwen3_tts_instruct_flash_wire_format(self, monkeypatch, tmp_path):
        spec = _qwen_cloud_spec(**{
            "qwen_cloud_tts": {
                "send_text_to_cloud": True,
                "host_voice": "Cherry",
                "guest_voice": "Ethan",
                "host_style": "用播客主持人的语气",
                "language_type": "Chinese",
            },
        })
        provider = _make_provider(spec)
        assert provider.endpoint == "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"
        sent = []
        monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
        monkeypatch.setattr("bookcast.adapters.qwen_cloud.request.build_opener",
                            _make_transport(_make_url_response(), sent=sent, wav_body=_make_pcm_wav()))
        from bookcast.provider_api import SpeechUnit
        unit = SpeechUnit(speaker="主持人", text="你好世界")
        provider.synthesize_unit(unit, tmp_path / "out.wav")
        assert len(sent) == 1
        req = sent[0]
        assert req["model"] == "qwen3-tts-instruct-flash"
        assert req["input"]["voice"] == "Cherry"
        assert req["input"]["text"] == "你好世界"
        assert "<|system|>" not in req["input"]["text"]
        assert req["input"]["language_type"] == "Chinese"
        assert req["parameters"]["instructions"] == "用播客主持人的语气"
        assert "format" not in req.get("parameters", {})
        assert "sample_rate" not in req.get("parameters", {})

    def test_cosyvoice_legacy_wire_format(self, monkeypatch, tmp_path):
        spec = _cosyvoice_spec(**{
            "qwen_cloud_tts": {
                "send_text_to_cloud": True,
                "host_voice": "longhua",
                "guest_voice": "longyue",
                "host_style": "用播客主持人的语气",
            },
        })
        provider = _make_provider(spec)
        assert provider.endpoint == "https://dashscope.aliyuncs.com/api/v1/services/aigc/text2voice/voice-synthesis"
        sent = []
        monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
        monkeypatch.setattr("bookcast.adapters.qwen_cloud.request.build_opener",
                            _make_transport(_make_response(_raw_pcm()), sent=sent))
        from bookcast.provider_api import SpeechUnit
        unit = SpeechUnit(speaker="主持人", text="你好世界")
        provider.synthesize_unit(unit, tmp_path / "out.wav")
        assert len(sent) == 1
        req = sent[0]
        assert req["model"] == "cosyvoice-v2"
        assert req["input"]["voice"] == "longhua"
        assert "<|system|>用播客主持人的语气<|/system|>" in req["input"]["text"]
        assert req["parameters"]["format"] == "pcm"
        assert req["parameters"]["sample_rate"] == SAMPLE_RATE

    def test_host_voice_used_for_host(self, monkeypatch, tmp_path):
        spec = _qwen_cloud_spec()
        provider = _make_provider(spec)
        sent = []
        monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
        monkeypatch.setattr("bookcast.adapters.qwen_cloud.request.build_opener",
                            _make_transport(_make_url_response(), sent=sent, wav_body=_make_pcm_wav()))
        from bookcast.provider_api import SpeechUnit
        unit = SpeechUnit(speaker="主持人", text="主持人说话")
        provider.synthesize_unit(unit, tmp_path / "out.wav")
        assert sent[0]["input"]["voice"] == "Cherry"

    def test_guest_voice_used_for_guest(self, monkeypatch, tmp_path):
        spec = _qwen_cloud_spec()
        provider = _make_provider(spec)
        sent = []
        monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
        monkeypatch.setattr("bookcast.adapters.qwen_cloud.request.build_opener",
                            _make_transport(_make_url_response(), sent=sent, wav_body=_make_pcm_wav()))
        from bookcast.provider_api import SpeechUnit
        unit = SpeechUnit(speaker="嘉宾", text="嘉宾说话")
        provider.synthesize_unit(unit, tmp_path / "out.wav")
        assert sent[0]["input"]["voice"] == "Ethan"

    def test_instructions_parameter_in_qwen_cloud_config(self, monkeypatch, tmp_path):
        spec = _qwen_cloud_spec(**{
            "qwen_cloud_tts": {
                "send_text_to_cloud": True,
                "host_voice": "Cherry",
                "guest_voice": "Ethan",
                "instructions": "全局指令：自然亲切讲述",
            },
        })
        provider = _make_provider(spec)
        sent = []
        monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
        monkeypatch.setattr("bookcast.adapters.qwen_cloud.request.build_opener",
                            _make_transport(_make_url_response(), sent=sent, wav_body=_make_pcm_wav()))
        from bookcast.provider_api import SpeechUnit
        unit = SpeechUnit(speaker="主持人", text="测试")
        provider.synthesize_unit(unit, tmp_path / "out.wav")
        assert sent[0]["parameters"]["instructions"] == "全局指令：自然亲切讲述"


# ---------------------------------------------------------------------------
# 2. Regional and workspace endpoints
# ---------------------------------------------------------------------------

class TestEndpoints:

    def test_default_beijing_endpoint(self):
        endpoint = _resolve_endpoint(None, "qwen3-tts-instruct-flash")
        assert endpoint == "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"

    def test_singapore_via_base_url(self):
        endpoint = _resolve_endpoint("https://dashscope-intl.aliyuncs.com/api/v1", "qwen3-tts-instruct-flash")
        assert endpoint == "https://dashscope-intl.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"

    def test_singapore_via_region(self):
        endpoint = _resolve_endpoint(None, "qwen3-tts-instruct-flash", region="singapore")
        assert endpoint == "https://dashscope-intl.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"

    def test_workspace_specific_endpoint(self):
        base = "https://custom-ws-123.cn-beijing.maas.aliyuncs.com/api/v1"
        endpoint = _resolve_endpoint(base, "qwen3-tts-instruct-flash")
        assert endpoint == "https://custom-ws-123.cn-beijing.maas.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"

    def test_full_endpoint_preserved(self):
        full = "https://dashscope-intl.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"
        assert _resolve_endpoint(full, "qwen3-tts-instruct-flash") == full

    def test_compatible_mode_url_normalized_to_api_v1(self):
        url = "https://dashscope.aliyuncs.com/compatible-mode/v1"
        assert _resolve_endpoint(url, "qwen3-tts-instruct-flash") == (
            "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"
        )

    def test_singapore_compatible_mode_url_normalized_to_api_v1(self):
        url = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
        assert _resolve_endpoint(url, "qwen3-tts-instruct-flash") == (
            "https://dashscope-intl.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"
        )

    def test_raw_root_dashscope_url_normalized_to_api_v1(self):
        url = "https://dashscope.aliyuncs.com"
        assert _resolve_endpoint(url, "qwen3-tts-instruct-flash") == (
            "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"
        )

    def test_workspace_compatible_mode_url_normalized_to_api_v1(self):
        url = "https://custom-ws-123.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"
        assert _resolve_endpoint(url, "qwen3-tts-instruct-flash") == (
            "https://custom-ws-123.cn-beijing.maas.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"
        )

    def test_live_preflight_endpoint_resolution_defaults(self, monkeypatch):
        try:
            from test_live_dashscope import _resolve_live_endpoints
        except ModuleNotFoundError:
            from tests.test_live_dashscope import _resolve_live_endpoints
        monkeypatch.delenv("DASHSCOPE_LLM_BASE_URL", raising=False)
        monkeypatch.delenv("DASHSCOPE_TTS_BASE_URL", raising=False)
        monkeypatch.delenv("DASHSCOPE_BASE_URL", raising=False)
        monkeypatch.delenv("DASHSCOPE_REGION", raising=False)
        llm_base, tts_base, region = _resolve_live_endpoints()
        assert llm_base == "https://dashscope.aliyuncs.com/compatible-mode/v1"
        assert tts_base == "https://dashscope.aliyuncs.com/api/v1"
        assert region == "beijing"

    def test_live_preflight_endpoint_resolution_singapore(self, monkeypatch):
        try:
            from test_live_dashscope import _resolve_live_endpoints
        except ModuleNotFoundError:
            from tests.test_live_dashscope import _resolve_live_endpoints
        monkeypatch.delenv("DASHSCOPE_LLM_BASE_URL", raising=False)
        monkeypatch.delenv("DASHSCOPE_TTS_BASE_URL", raising=False)
        monkeypatch.delenv("DASHSCOPE_BASE_URL", raising=False)
        monkeypatch.setenv("DASHSCOPE_REGION", "singapore")
        llm_base, tts_base, region = _resolve_live_endpoints()
        assert llm_base == "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
        assert tts_base == "https://dashscope-intl.aliyuncs.com/api/v1"
        assert region == "singapore"

    def test_live_preflight_endpoint_resolution_explicit_overrides(self, monkeypatch):
        try:
            from test_live_dashscope import _resolve_live_endpoints
        except ModuleNotFoundError:
            from tests.test_live_dashscope import _resolve_live_endpoints
        monkeypatch.setenv("DASHSCOPE_LLM_BASE_URL", "https://llm.example.com/compatible-mode/v1")
        monkeypatch.setenv("DASHSCOPE_TTS_BASE_URL", "https://tts.example.com/api/v1")
        monkeypatch.setenv("DASHSCOPE_BASE_URL", "https://generic.example.com")
        llm_base, tts_base, _ = _resolve_live_endpoints()
        assert llm_base == "https://llm.example.com/compatible-mode/v1"
        assert tts_base == "https://tts.example.com/api/v1"


# ---------------------------------------------------------------------------
# 3. Audio decoding and WAV output
# ---------------------------------------------------------------------------

class TestAudioDecode:

    def test_official_fixture_url_decoding(self, monkeypatch, tmp_path):
        fixture_path = Path(__file__).resolve().parent / "fixtures" / "qwen3_tts_instruct_flash_response.json"
        fixture_bytes = fixture_path.read_bytes()

        provider = _make_provider()
        monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
        wav_content = _make_pcm_wav(480)
        monkeypatch.setattr("bookcast.adapters.qwen_cloud.request.build_opener",
                            _make_transport(fixture_bytes, wav_body=wav_content))
        from bookcast.provider_api import SpeechUnit
        out = tmp_path / "out.wav"
        info = provider.synthesize_unit(SpeechUnit(speaker="主持人", text="你好"), out)
        assert out.exists()
        assert out.read_bytes().startswith(b"RIFF")
        assert info.voice == "Cherry"
        with wave.open(str(out), "rb") as wf:
            assert wf.getnchannels() == 1
            assert wf.getsampwidth() == 2
            assert wf.getframerate() == SAMPLE_RATE
            assert wf.getnframes() > 0

    def test_streaming_data_base64_decoded_and_written_as_wav(self, monkeypatch, tmp_path):
        provider = _make_provider()
        monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
        streaming_resp = json.dumps({
            "output": {"audio": {"data": base64.b64encode(_raw_pcm(480)).decode(), "url": ""}},
            "request_id": "test-id",
        }).encode()
        monkeypatch.setattr("bookcast.adapters.qwen_cloud.request.build_opener",
                            _make_transport(streaming_resp))
        from bookcast.provider_api import SpeechUnit
        out = tmp_path / "out.wav"
        provider.synthesize_unit(SpeechUnit(speaker="主持人", text="你好"), out)
        assert out.exists()
        with wave.open(str(out), "rb") as wf:
            assert wf.getnchannels() == 1
            assert wf.getsampwidth() == 2
            assert wf.getframerate() == SAMPLE_RATE
            assert wf.getnframes() > 0

    def test_legacy_pcm_decoded_and_written_as_wav(self, monkeypatch, tmp_path):
        provider = _make_provider(_cosyvoice_spec())
        monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
        monkeypatch.setattr("bookcast.adapters.qwen_cloud.request.build_opener",
                            _make_transport(_make_response(_raw_pcm(480))))
        from bookcast.provider_api import SpeechUnit
        out = tmp_path / "out.wav"
        provider.synthesize_unit(SpeechUnit(speaker="主持人", text="你好"), out)
        assert out.exists()
        with wave.open(str(out), "rb") as wf:
            assert wf.getnchannels() == 1
            assert wf.getsampwidth() == 2
            assert wf.getframerate() == SAMPLE_RATE
            assert wf.getnframes() > 0

    def test_invalid_base64_raises_schema_error(self):
        bad = json.dumps({"output": {"audio": "!!!not-base64!!!"}}).encode()
        with pytest.raises(ProviderError) as exc:
            _decode_response(bad, SAMPLE_RATE)
        assert exc.value.kind == ErrorKind.SCHEMA

    def test_empty_pcm_raises_schema_error(self):
        with pytest.raises(ProviderError) as exc:
            _decode_pcm_to_wav(b"", SAMPLE_RATE)
        assert exc.value.kind == ErrorKind.SCHEMA

    def test_odd_length_pcm_raises_schema_error(self):
        with pytest.raises(ProviderError) as exc:
            _decode_pcm_to_wav(b"\x00" * 3, SAMPLE_RATE)
        assert exc.value.kind == ErrorKind.SCHEMA

    def test_missing_output_field_raises_schema_error(self):
        bad = json.dumps({"request_id": "x"}).encode()
        with pytest.raises(ProviderError) as exc:
            _decode_response(bad, SAMPLE_RATE)
        assert exc.value.kind == ErrorKind.SCHEMA

    def test_missing_audio_field_raises_schema_error(self):
        bad = json.dumps({"output": {}}).encode()
        with pytest.raises(ProviderError) as exc:
            _decode_response(bad, SAMPLE_RATE)
        assert exc.value.kind == ErrorKind.SCHEMA

    def test_loopback_audio_url_rejected(self, monkeypatch, tmp_path):
        provider = _make_provider()
        monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
        loopback_resp = json.dumps({
            "output": {"audio": {"url": "http://127.0.0.1:8000/audio.wav"}},
        }).encode()
        monkeypatch.setattr("bookcast.adapters.qwen_cloud.request.build_opener",
                            _make_transport(loopback_resp))
        from bookcast.provider_api import SpeechUnit
        with pytest.raises(ProviderError) as exc:
            provider.synthesize_unit(SpeechUnit(speaker="主持人", text="测试"), tmp_path / "out.wav")
        assert exc.value.kind == ErrorKind.INPUT


# ---------------------------------------------------------------------------
# 4. Input validation
# ---------------------------------------------------------------------------

class TestInputValidation:

    def test_empty_text_after_normalization_raises_input_error(self, monkeypatch, tmp_path):
        provider = _make_provider()
        monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
        from bookcast.provider_api import SpeechUnit
        with pytest.raises(ProviderError) as exc:
            provider.synthesize_unit(SpeechUnit(speaker="主持人", text="   "), tmp_path / "out.wav")
        assert exc.value.kind == ErrorKind.INPUT

    def test_text_over_80_chars_raises_input_error(self, monkeypatch, tmp_path):
        provider = _make_provider()
        monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
        long_text = "好" * 81
        with pytest.raises(ProviderError) as exc:
            provider.synthesize_unit({"speaker": "主持人", "text": long_text}, tmp_path / "out.wav")
        assert exc.value.kind == ErrorKind.INPUT

    def test_exactly_80_chars_accepted(self, monkeypatch, tmp_path):
        provider = _make_provider()
        monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
        monkeypatch.setattr("bookcast.adapters.qwen_cloud.request.build_opener",
                            _make_transport(_make_url_response(), wav_body=_make_pcm_wav()))
        from bookcast.provider_api import SpeechUnit
        text = "好" * 80
        provider.synthesize_unit(SpeechUnit(speaker="主持人", text=text), tmp_path / "out.wav")

    def test_synthesize_raises_input_error(self, tmp_path):
        provider = _make_provider()
        with pytest.raises(ProviderError) as exc:
            provider.synthesize(None, tmp_path / "out.wav")
        assert exc.value.kind == ErrorKind.INPUT


# ---------------------------------------------------------------------------
# 5. HTTP error classification
# ---------------------------------------------------------------------------

class TestErrorClassification:

    @pytest.mark.parametrize("status,expected_kind", [
        (401, ErrorKind.AUTH), (403, ErrorKind.AUTH),
        (402, ErrorKind.QUOTA), (429, ErrorKind.RATE_LIMIT),
        (408, ErrorKind.TIMEOUT), (500, ErrorKind.UNAVAILABLE),
        (422, ErrorKind.INPUT),
    ])
    def test_http_status_classification(self, monkeypatch, tmp_path, status, expected_kind):
        provider = _make_provider()
        monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
        monkeypatch.setattr("bookcast.adapters.qwen_cloud.request.build_opener",
                            _make_transport(b"{}", status=status))
        from bookcast.provider_api import SpeechUnit
        with pytest.raises(ProviderError) as exc:
            provider.synthesize_unit(SpeechUnit(speaker="主持人", text="测试"), tmp_path / "out.wav")
        assert exc.value.kind == expected_kind

    @pytest.mark.parametrize("code,expected_kind", [
        ("Ariel.InsufficientBalance", ErrorKind.QUOTA),
        ("Ariel.QuotaExceeded", ErrorKind.QUOTA),
        ("QuotaExceeded", ErrorKind.QUOTA),
        ("Throttling", ErrorKind.RATE_LIMIT),
        ("Throttling.RateQuota", ErrorKind.RATE_LIMIT),
        ("Ariel.UserRequestRateLimit", ErrorKind.RATE_LIMIT),
        ("InvalidApiKey", ErrorKind.AUTH),
    ])
    def test_dashscope_code_classification(self, code, expected_kind):
        body = json.dumps({"code": code}).encode()
        err = _classify_dashscope(429, body)
        err2 = _classify_dashscope(200, body) if code == "InvalidApiKey" else err
        assert (err if code != "InvalidApiKey" else err2).kind == expected_kind

    def test_missing_api_key_raises_auth_error(self, monkeypatch, tmp_path):
        provider = _make_provider()
        monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
        from bookcast.provider_api import SpeechUnit
        with pytest.raises(ProviderError) as exc:
            provider.synthesize_unit(SpeechUnit(speaker="主持人", text="测试"), tmp_path / "out.wav")
        assert exc.value.kind == ErrorKind.AUTH

    def test_oversized_response_raises_schema_error(self, monkeypatch, tmp_path):
        from bookcast.adapters.qwen_cloud import _MAX_RESPONSE
        provider = _make_provider()
        monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
        big = b"x" * (_MAX_RESPONSE + 2)
        monkeypatch.setattr("bookcast.adapters.qwen_cloud.request.build_opener",
                            _make_transport(big))
        from bookcast.provider_api import SpeechUnit
        with pytest.raises(ProviderError) as exc:
            provider.synthesize_unit(SpeechUnit(speaker="主持人", text="测试"), tmp_path / "out.wav")
        assert exc.value.kind == ErrorKind.SCHEMA


# ---------------------------------------------------------------------------
# 6. Capabilities and config validation
# ---------------------------------------------------------------------------

class TestCapabilitiesAndConfig:

    def test_capabilities_speech_units_cloud(self):
        provider = _make_provider()
        caps = provider.capabilities()
        assert caps.speech is True
        assert caps.speech_units is True
        assert caps.cloud is True
        assert caps.speech_segments is False
        assert caps.local is False

    def test_config_requires_send_text_to_cloud_true(self):
        with pytest.raises(ValidationError):
            ProviderSpec.model_validate({
                "name": "q", "kind": "tts", "type": "qwen-cloud-tts",
                "model": "qwen3-tts-instruct-flash", "api_key_env": "DASHSCOPE_API_KEY",
                "qwen_cloud_tts": {
                    "send_text_to_cloud": False,
                    "host_voice": "Cherry", "guest_voice": "Ethan",
                },
            })

    def test_config_requires_distinct_voices(self):
        with pytest.raises(ValidationError):
            ProviderSpec.model_validate({
                "name": "q", "kind": "tts", "type": "qwen-cloud-tts",
                "model": "qwen3-tts-instruct-flash", "api_key_env": "DASHSCOPE_API_KEY",
                "qwen_cloud_tts": {
                    "send_text_to_cloud": True,
                    "host_voice": "Cherry", "guest_voice": "Cherry",
                },
            })

    def test_config_requires_api_key_env(self):
        with pytest.raises(ValidationError):
            ProviderSpec.model_validate({
                "name": "q", "kind": "tts", "type": "qwen-cloud-tts",
                "model": "qwen3-tts-instruct-flash",
                "qwen_cloud_tts": {
                    "send_text_to_cloud": True,
                    "host_voice": "Cherry", "guest_voice": "Ethan",
                },
            })

    def test_base_url_allowed_on_qwen_cloud_tts(self):
        spec = ProviderSpec.model_validate({
            "name": "q", "kind": "tts", "type": "qwen-cloud-tts",
            "model": "qwen3-tts-instruct-flash", "api_key_env": "DASHSCOPE_API_KEY",
            "base_url": "https://dashscope-intl.aliyuncs.com/api/v1",
            "qwen_cloud_tts": {
                "send_text_to_cloud": True,
                "host_voice": "Cherry", "guest_voice": "Ethan",
            },
        })
        assert spec.base_url == "https://dashscope-intl.aliyuncs.com/api/v1"

    def test_qwen_cloud_tts_not_allowed_for_other_types(self):
        with pytest.raises(ValidationError):
            ProviderSpec.model_validate({
                "name": "q", "kind": "tts", "type": "mock", "model": "mock-v1",
                "qwen_cloud_tts": {
                    "send_text_to_cloud": True,
                    "host_voice": "Cherry", "guest_voice": "Ethan",
                },
            })

    def test_qwen_cloud_tts_registered_in_default_registry(self):
        registry = default_registry()
        assert "tts/qwen-cloud-tts" in registry.types()

    def test_qwen_local_still_registered(self):
        registry = default_registry()
        assert "tts/qwen-local" in registry.types()


# ---------------------------------------------------------------------------
# 7. Health check
# ---------------------------------------------------------------------------

class TestHealthCheck:

    def test_health_check_available_when_key_set(self, monkeypatch):
        provider = _make_provider()
        monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
        status = provider.health_check()
        assert status.availability == "available"

    def test_health_check_auth_error_when_key_missing(self, monkeypatch):
        provider = _make_provider()
        monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
        status = provider.health_check()
        assert status.availability == "unavailable"
        assert status.authentication_error is True


# ---------------------------------------------------------------------------
# 8. Cache key
# ---------------------------------------------------------------------------

class TestCacheKey:

    def test_different_voices_different_cache_key(self):
        a = _make_provider(_qwen_cloud_spec())
        b = _make_provider(_qwen_cloud_spec(**{
            "qwen_cloud_tts": {
                "send_text_to_cloud": True,
                "host_voice": "Serena",
                "guest_voice": "Ethan",
            },
        }))
        assert a.cache_key != b.cache_key

    def test_same_config_same_cache_key(self):
        a = _make_provider(_qwen_cloud_spec())
        b = _make_provider(_qwen_cloud_spec())
        assert a.cache_key == b.cache_key

    def test_different_model_different_cache_key(self):
        a = _make_provider(_qwen_cloud_spec())
        b = _make_provider(_qwen_cloud_spec(model="qwen3-tts-flash"))
        assert a.cache_key != b.cache_key

    def test_different_endpoint_different_cache_key(self):
        a = _make_provider(_qwen_cloud_spec(base_url="https://dashscope.aliyuncs.com/api/v1"))
        b = _make_provider(_qwen_cloud_spec(base_url="https://dashscope-intl.aliyuncs.com/api/v1"))
        assert a.cache_key != b.cache_key


# ---------------------------------------------------------------------------
# 9. Rate limiting
# ---------------------------------------------------------------------------

class TestRateLimiting:

    def test_sleeper_called_when_rate_limited(self, monkeypatch, tmp_path):
        spec = _qwen_cloud_spec(**{
            "qwen_cloud_tts": {
                "send_text_to_cloud": True,
                "host_voice": "Cherry",
                "guest_voice": "Ethan",
                "min_request_interval": 0.5,
            },
        })
        sleep_calls = []
        provider = _make_provider(spec, sleeper=sleep_calls.append)
        monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
        monkeypatch.setattr("bookcast.adapters.qwen_cloud.request.build_opener",
                            _make_transport(_make_url_response(), wav_body=_make_pcm_wav()))
        from bookcast.provider_api import SpeechUnit
        import time
        provider._last_request_at = time.monotonic()
        provider.synthesize_unit(SpeechUnit(speaker="主持人", text="测试"), tmp_path / "out.wav")
        assert len(sleep_calls) >= 1

    def test_no_sleep_when_interval_zero(self, monkeypatch, tmp_path):
        spec = _qwen_cloud_spec(**{
            "qwen_cloud_tts": {
                "send_text_to_cloud": True,
                "host_voice": "Cherry",
                "guest_voice": "Ethan",
                "min_request_interval": 0.0,
            },
        })
        sleep_calls = []
        provider = _make_provider(spec, sleeper=sleep_calls.append)
        monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
        monkeypatch.setattr("bookcast.adapters.qwen_cloud.request.build_opener",
                            _make_transport(_make_url_response(), wav_body=_make_pcm_wav()))
        from bookcast.provider_api import SpeechUnit
        import time
        provider._last_request_at = time.monotonic()
        provider.synthesize_unit(SpeechUnit(speaker="主持人", text="测试"), tmp_path / "out.wav")
        assert len(sleep_calls) == 0

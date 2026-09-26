"""Opt-in live smoke tests for Alibaba Cloud DashScope (Qwen3.7-Flash & Qwen3-TTS-Instruct-Flash).

Default pytest runs completely offline. To run this live smoke test:
    BOOKCAST_RUN_DASHSCOPE_LIVE=1 DASHSCOPE_API_KEY="sk-..." .venv/bin/pytest tests/test_live_dashscope.py -m live -s
"""

import os
from pathlib import Path
import wave

import pytest

from bookcast.adapters.qwen_llm import QwenLLMProvider
from bookcast.adapters.qwen_cloud import QwenCloudTTSProvider
from bookcast.provider_api import ProviderRequestContext, SpeechUnit
from bookcast.provider_config import ProviderSpec

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        os.environ.get("BOOKCAST_RUN_DASHSCOPE_LIVE") != "1",
        reason="requires explicit BOOKCAST_RUN_DASHSCOPE_LIVE=1 opt-in",
    ),
]


def _check_api_key():
    key = os.environ.get("DASHSCOPE_API_KEY", "").strip()
    if not key:
        pytest.skip("DASHSCOPE_API_KEY environment variable not set")


def _resolve_live_endpoints() -> tuple[str, str, str]:
    """Resolve LLM base URL, TTS base URL, and region from environment.

    Precedence:
      LLM: DASHSCOPE_LLM_BASE_URL -> DASHSCOPE_BASE_URL -> DASHSCOPE_REGION (beijing default)
      TTS: DASHSCOPE_TTS_BASE_URL -> DASHSCOPE_BASE_URL -> DASHSCOPE_REGION (beijing default)
    """
    region = os.environ.get("DASHSCOPE_REGION", "beijing").strip().lower()
    generic = os.environ.get("DASHSCOPE_BASE_URL", "").strip()

    llm_env = os.environ.get("DASHSCOPE_LLM_BASE_URL", "").strip()
    if llm_env:
        llm_base = llm_env
    elif generic:
        llm_base = generic
    elif region in ("singapore", "intl", "ap-southeast-1"):
        llm_base = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
    else:
        llm_base = "https://dashscope.aliyuncs.com/compatible-mode/v1"

    tts_env = os.environ.get("DASHSCOPE_TTS_BASE_URL", "").strip()
    if tts_env:
        tts_base = tts_env
    elif generic:
        tts_base = generic
    elif region in ("singapore", "intl", "ap-southeast-1"):
        tts_base = "https://dashscope-intl.aliyuncs.com/api/v1"
    else:
        tts_base = "https://dashscope.aliyuncs.com/api/v1"

    return llm_base, tts_base, region


def test_live_qwen3_7_flash_minimal_text():
    """Live smoke test: minimal 1-prompt call to Qwen3.7-Flash."""
    _check_api_key()
    llm_base, _, _ = _resolve_live_endpoints()
    spec = ProviderSpec.model_validate({
        "name": "qwen-live",
        "kind": "llm",
        "type": "qwen-llm",
        "model": "qwen3.7-flash",
        "base_url": llm_base,
        "api_key_env": "DASHSCOPE_API_KEY",
        "timeout_seconds": 30,
    })
    provider = QwenLLMProvider(spec)
    prompt = "请回复两个字：收到。"
    response = provider.generate(prompt)
    assert response and len(response.strip()) > 0
    assert provider.last_usage is not None


def test_live_qwen3_tts_instruct_flash_chinese_tts(tmp_path):
    """Live smoke test: 20~30 character Chinese speech synthesis with instructions."""
    _check_api_key()
    _, tts_base, region = _resolve_live_endpoints()
    spec = ProviderSpec.model_validate({
        "name": "qwen-tts-live",
        "kind": "tts",
        "type": "qwen-cloud-tts",
        "model": "qwen3-tts-instruct-flash",
        "base_url": tts_base,
        "api_key_env": "DASHSCOPE_API_KEY",
        "timeout_seconds": 30,
        "qwen_cloud_tts": {
            "send_text_to_cloud": True,
            "region": region if region in ("singapore", "intl", "ap-southeast-1") else "beijing",
            "host_voice": "Cherry",
            "guest_voice": "Ethan",
            "language_type": "Chinese",
            "host_style": "自然沉稳的中文播客主持人，语速适中，富有亲和力。",
        },
    })
    provider = QwenCloudTTSProvider(spec)
    unit = SpeechUnit(speaker="主持人", text="欢迎收听 BookCast 播客节目，我们今天讨论一本经典著作。")
    out_wav = tmp_path / "smoke.wav"

    telemetry_file = tmp_path / "usage" / "physical_requests.jsonl"
    context = ProviderRequestContext(
        job_id="live-smoke",
        output_id="out-smoke",
        logical_chunk_id="tts:0001:0001",
        provider=spec.name,
        model=spec.model,
        telemetry_path=telemetry_file,
    )

    info = provider.synthesize_unit_with_context(unit, out_wav, context=context)
    assert out_wav.exists()
    assert out_wav.stat().st_size > 0
    with wave.open(str(out_wav), "rb") as wf:
        assert wf.getnchannels() == 1
        assert wf.getsampwidth() == 2
        assert wf.getnframes() > 0
    assert info.voice == "Cherry"
    assert telemetry_file.exists()

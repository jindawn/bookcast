"""Test Gemini 3.8 Flash-Lite TTS official billing unit and currency conversion."""

import json
from pathlib import Path

import pytest

from bookcast.cost import calculate_cost_summary
from bookcast.generation import ProviderUsage
from bookcast.models import AIAttempt
from bookcast.provider_config import ProvidersConfig, load_config


def test_gemini_tts_pricing_schema_and_units():
    """Verify Gemini TTS pricing schema parses currency, billing_unit and rates correctly."""
    config = load_config(Path("bookcast.toml"))
    gemini_pricing = config.pricing.get("gemini-3.8-flash-lite-tts")
    assert gemini_pricing is not None
    assert gemini_pricing.default is not None
    assert gemini_pricing.default.currency == "USD"
    assert gemini_pricing.default.billing_unit == "audio_duration"
    assert gemini_pricing.default.audio_seconds_per_million == 150.00
    assert gemini_pricing.default.audio_tokens_per_million == 6.00
    assert gemini_pricing.default.uncached_input_per_million == 0.50
    assert gemini_pricing.default.characters_per_million == 0.0


def test_qwen_tts_pricing_schema():
    """Verify Qwen TTS pricing schema retains characters billing at 80 CNY / 1M chars."""
    config = load_config(Path("bookcast.toml"))
    qwen_pricing = config.pricing.get("qwen3-tts-instruct-flash")
    assert qwen_pricing is not None
    assert qwen_pricing.default is not None
    assert qwen_pricing.default.currency == "CNY"
    assert qwen_pricing.default.billing_unit == "characters"
    assert qwen_pricing.default.characters_per_million == 80.00


def test_gemini_tts_cost_calculation_duration_and_tokens(tmp_path):
    """Verify Gemini TTS costs correctly compute audio duration in USD and convert to CNY."""
    config = ProvidersConfig.model_validate({
        "providers": [
            {"name": "mock-llm", "kind": "llm", "type": "mock", "model": "mock-llm-v1"},
            {"name": "gemini-tts", "kind": "tts", "type": "gemini-tts", "model": "gemini-3.8-flash-lite-tts",
             "api_key_env": "GEMINI_API_KEY", "cloud_tts": {"send_text_to_cloud": True}},
        ],
        "llm_priority": ["mock-llm"],
        "tts_priority": ["gemini-tts"],
        "exchange_rates": {"USD": 7.20},
        "pricing": {
            "gemini-3.8-flash-lite-tts": {
                "default": {
                    "currency": "USD",
                    "billing_unit": "audio_duration",
                    "audio_seconds_per_million": 150.00,  # $0.0015 / 10s
                    "uncached_input_per_million": 0.50,   # $0.50 / 1M text tokens
                }
            }
        },
    })

    # Unit sidecar with duration 100.0s (100 * $0.00015 = $0.0150 USD)
    sidecar = tmp_path / "audio" / "segments" / "0001-0001.json"
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    sidecar.write_text(json.dumps({
        "duration_seconds": 100.0,
        "character_count": 500,
    }))

    call = AIAttempt(
        id="call-gemini-1",
        task="tts_segment:0001:0001",
        kind="tts",
        provider="gemini-tts",
        model="gemini-3.8-flash-lite-tts",
        prompt_version="v1",
        input_hash="a" * 64,
        status="completed",
        artifacts={"audio/segments/0001-0001.json": "hash"},
        provider_reported_usage=ProviderUsage(input_tokens=1000, output_tokens=0),
    )

    summary = calculate_cost_summary([call], config, root_dir=tmp_path)
    row = summary["tts"]["providers"]["gemini-tts::gemini-3.8-flash-lite-tts"]

    # Audio: 100s * $0.00015 = $0.015 USD
    # Text input: 1000 tokens * $0.50 / 1M = $0.0005 USD
    # Total USD: $0.0155 USD
    assert row["original_currency"] == "USD"
    assert round(row["original_cost"], 4) == 0.0155
    assert row["exchange_rate"] == 7.20
    assert row["converted_currency"] == "CNY"
    # Converted CNY: 0.0155 * 7.2 = 0.1116 CNY
    assert round(row["converted_cost"], 4) == 0.1116
    assert round(row["cost"]["amount"], 4) == 0.1116


def test_gemini_tts_cost_calculation_audio_tokens(tmp_path):
    """Verify Gemini TTS costs calculate from audio_tokens when provided."""
    config = ProvidersConfig.model_validate({
        "providers": [
            {"name": "mock-llm", "kind": "llm", "type": "mock", "model": "mock-llm-v1"},
            {"name": "gemini-tts", "kind": "tts", "type": "gemini-tts", "model": "gemini-3.8-flash-lite-tts",
             "api_key_env": "GEMINI_API_KEY", "cloud_tts": {"send_text_to_cloud": True}},
        ],
        "llm_priority": ["mock-llm"],
        "tts_priority": ["gemini-tts"],
        "exchange_rates": {"USD": 7.20},
        "pricing": {
            "gemini-3.8-flash-lite-tts": {
                "default": {
                    "currency": "USD",
                    "billing_unit": "audio_tokens",
                    "audio_tokens_per_million": 6.00,
                    "audio_seconds_per_million": 150.00,
                    "uncached_input_per_million": 0.50,
                }
            }
        },
    })

    sidecar = tmp_path / "audio" / "segments" / "0001-0001.json"
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    sidecar.write_text(json.dumps({"duration_seconds": 10.0, "character_count": 50}))

    # 250 audio tokens (equivalent to 10s audio at $6/M)
    call = AIAttempt(
        id="call-gemini-2",
        task="tts_segment:0001:0001",
        kind="tts",
        provider="gemini-tts",
        model="gemini-3.8-flash-lite-tts",
        prompt_version="v1",
        input_hash="b" * 64,
        status="completed",
        artifacts={"audio/segments/0001-0001.json": "hash"},
        provider_reported_usage=ProviderUsage(input_tokens=100, output_tokens=250),
    )

    summary = calculate_cost_summary([call], config, root_dir=tmp_path)
    row = summary["tts"]["providers"]["gemini-tts::gemini-3.8-flash-lite-tts"]
    # 250 * 6.0 / 1M = $0.0015 USD
    # Input: 100 * 0.5 / 1M = $0.00005 USD
    # Total USD: $0.00155 USD
    assert round(row["original_cost"], 5) == 0.00155
    assert row["original_currency"] == "USD"

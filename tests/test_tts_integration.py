import pytest
import os
import json
from pathlib import Path
from unittest.mock import patch, MagicMock

from bookcast.provider_config import ProvidersConfig, CloudTTSConfig
from bookcast.provider_registry import default_registry
from bookcast.provider_api import ProviderError, ErrorKind, SpeechSegment, SpeechTurn
from bookcast.speech_segments import speech_segments, render_segments
from bookcast.pipeline import Pipeline
from bookcast.llm_usage import tts_usage_snapshot
from bookcast.cli import app
from typer.testing import CliRunner

runner = CliRunner()

def test_registry_creates_providers():
    registry = default_registry()
    # Test Kokoro
    spec_kokoro = ProvidersConfig.model_validate({
        'schema_version': 1, 'llm_priority': ['mock'], 'tts_priority': ['kokoro'], 'providers': [{'name': 'mock', 'kind': 'llm', 'type': 'mock', 'model': 'm'}, {'name': 'kokoro', 'kind': 'tts', 'type': 'kokoro-local', 'model': 'kokoro-multi-lang-v1_0', 'local_tts': {'model_dir': '.'}}]
    }).providers[1]
    p_kokoro = registry.create(spec_kokoro)
    assert p_kokoro.name == 'kokoro'
    
    # Test Gemini
    spec_gemini = ProvidersConfig.model_validate({
        'schema_version': 1, 'llm_priority': ['mock'], 'tts_priority': ['gemini'], 'providers': [{'name': 'mock', 'kind': 'llm', 'type': 'mock', 'model': 'm'}, {'name': 'gemini', 'kind': 'tts', 'type': 'gemini-tts', 'model': 'gemini-3.8-flash-tts', 'api_key_env': 'GEMINI_API_KEY', 'cloud_tts': {'send_text_to_cloud': True}}]
    }).providers[1]
    p_gemini = registry.create(spec_gemini)
    assert p_gemini.name == 'gemini'
    assert p_gemini.settings.host_voice == 'Kore'  # Default mapping
    assert p_gemini.settings.guest_voice == 'Puck'

def test_gemini_missing_api_key_fail_fast(monkeypatch):
    registry = default_registry()
    spec_gemini = ProvidersConfig.model_validate({
        'schema_version': 1, 'llm_priority': ['mock'], 'tts_priority': ['gemini'], 'providers': [{'name': 'mock', 'kind': 'llm', 'type': 'mock', 'model': 'm'}, {'name': 'gemini', 'kind': 'tts', 'type': 'gemini-tts', 'model': 'gemini-3.8-flash-tts', 'api_key_env': 'FAKE_API_KEY', 'cloud_tts': {'send_text_to_cloud': True}}]
    }).providers[1]
    p_gemini = registry.create(spec_gemini)

    # 1. Env var absent
    monkeypatch.delenv('FAKE_API_KEY', raising=False)
    with pytest.raises(ProviderError) as exc:
        p_gemini._request({})
    assert exc.value.kind == ErrorKind.AUTH
    assert not isinstance(exc.value, SystemExit)
    assert not isinstance(exc.value, UnboundLocalError)

    # 2. Env var empty
    monkeypatch.setenv('FAKE_API_KEY', '')
    with pytest.raises(ProviderError) as exc:
        p_gemini._request({})
    assert exc.value.kind == ErrorKind.AUTH

    monkeypatch.setenv('FAKE_API_KEY', '   ')
    with pytest.raises(ProviderError) as exc:
        p_gemini._request({})
    assert exc.value.kind == ErrorKind.AUTH

    # 3. Valid key proceeds past auth
    secret_val = 'AIzaSySecretValidKey12345'
    monkeypatch.setenv('FAKE_API_KEY', secret_val)
    with pytest.raises(Exception) as exc_valid:
        # Will fail at transport or network, NOT at auth check
        p_gemini._request({})
    assert not (isinstance(exc_valid.value, ProviderError) and exc_valid.value.kind == ErrorKind.AUTH)
    assert secret_val not in str(exc_valid.value)

def test_config_overrides_voice():
    spec_gemini = ProvidersConfig.model_validate({
        'schema_version': 1, 'llm_priority': ['mock'], 'tts_priority': ['gemini'], 'providers': [{'name': 'mock', 'kind': 'llm', 'type': 'mock', 'model': 'm'}, {'name': 'gemini', 'kind': 'tts', 'type': 'gemini-tts', 'model': 'gemini-3.8-flash-tts', 'api_key_env': 'GEMINI_API_KEY', 'cloud_tts': {'send_text_to_cloud': True, 'host_voice': 'NewHost', 'host_style': 'Custom Style'}}]
    }).providers[1]
    assert spec_gemini.cloud_tts.host_voice == 'NewHost'
    assert spec_gemini.cloud_tts.host_style == 'Custom Style'

def test_chunking_27_turns():
    class MockPodcastScript:
        def __init__(self, turns):
            self.turns = turns
            self.chapter_id = "0001"
            
    # Create 27 turns
    turns = [SpeechTurn(speaker="主持人" if i % 2 == 0 else "嘉宾", text=f"这是第{i}句较长的话。" * 10) for i in range(27)]
    script = MockPodcastScript(turns)
    chunks = list(speech_segments(script))
    
    assert len(chunks) > 1
    # Check no turns lost or duplicated
    total_turns = sum(len(segment.turns) for idx, segment in chunks)
    assert total_turns == 27
    # Check order
    reconstructed = [t.speaker for idx, segment in chunks for t in segment.turns]
    expected = [t.speaker for t in turns]
    assert reconstructed == expected

def test_tts_usage_snapshot_missing_usage():
    class MockCall:
        def __init__(self, status):
            self.kind = 'tts'
            self.status = status
            self.provider = 'gemini'
            self.model = 'm1'
            self.provider_reported_usage = None
            self.artifacts = []
    
    calls = [MockCall('completed'), MockCall('failed_retryable')]
    snapshot = tts_usage_snapshot(calls)
    usage = snapshot['tts_usage'][0]
    assert usage['request_count'] == 2
    assert usage['retry_count'] == 1
    assert 'input_tokens' not in usage
    assert 'output_tokens' not in usage
    assert usage['usage_available'] is False

def test_cli_help_tts_provider():
    result = runner.invoke(app, ["generate", "--help"])
    assert "--tts-provider" in result.stdout
    assert "auto" in result.stdout

import pytest
import io
import json
from pathlib import Path
from bookcast.adapters.gemini import GeminiTTSProvider, sanitize_gemini_error
from bookcast.models import PodcastScript, DialogueTurn
from test_gemini_retry import FakeClock, FakeSleeper, install_transport, http_error

def test_sanitizer():
    # A. Fake API key
    msg1 = "Invalid key AIzaSyAABBCCDD1234567890abcdefghijk1234"
    assert "AIza" not in sanitize_gemini_error(msg1)
    
    # B & C. Transcript and Base64
    msg2 = "Transcript: The weather is nice. " + "A" * 300
    sanitized = sanitize_gemini_error(msg2)
    assert len(sanitized) < 200
    assert "REDACTED" in sanitized
    
    msg3 = "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    assert "Bearer" in sanitize_gemini_error(msg3)
    assert "eyJ" not in sanitize_gemini_error(msg3)

class MockSpec:
    name = "gemini"
    model = "gemini-test"
    api_key_env = "TEST_KEY"
    timeout_seconds = 30
    cloud_tts = None
    
def get_provider():
    return GeminiTTSProvider(MockSpec(), clock=FakeClock(), sleeper=FakeSleeper(FakeClock()))

def test_accounting_timeout_then_success(monkeypatch, tmp_path):
    monkeypatch.setenv('TEST_KEY', 'abc')
    provider = get_provider()
    requests = []
    
    def opening(req, timeout):
        requests.append(req)
        if len(requests) == 1:
            raise TimeoutError("connection timeout")
        return io.BytesIO(json.dumps({'audio': 'mock', 'usage_metadata': {'prompt_token_count': 10, 'candidates_token_count': 20}}).encode())
        
    install_transport(monkeypatch, opening)
    dest = tmp_path / 'artifacts' / 'audio' / 'segments' / '0001-1.wav'
    dest.parent.mkdir(parents=True, exist_ok=True)
    
    # Run
    provider._request({'mock': 'payload'}, destination=dest)
    
    # Assert logs
    log_file = tmp_path / 'usage' / 'physical_requests.jsonl'
    assert log_file.exists()
    lines = log_file.read_text().splitlines()
    assert len(lines) == 2
    
    # First request: Timeout
    req1 = json.loads(lines[0])
    assert req1['http_status'] is None
    assert req1['result'] == 'failed'
    assert req1['retry_reason'] == 'timeout'
    assert req1['billing_status'] == 'unknown'
    assert req1['chunk_id'] == '0001-1'
    assert req1['physical_attempt_index'] == 0
    
    # Second request: Success
    req2 = json.loads(lines[1])
    assert req2['http_status'] == 200
    assert req2['result'] == 'success'
    assert req2['retry_reason'] is None
    assert req2['billing_status'] == 'billed'
    assert req2['usage_available'] is True
    assert req2['physical_attempt_index'] == 1
    
class MockSpec:
    name = "gemini"
    model = "gemini-test"
    api_key_env = "TEST_KEY"
    timeout_seconds = 30
    cloud_tts = None
    
def get_provider():
    return GeminiTTSProvider(MockSpec(), clock=FakeClock(), sleeper=FakeSleeper(FakeClock()))

def test_accounting_normal_success(monkeypatch, tmp_path):
    monkeypatch.setenv('TEST_KEY', 'abc')
    provider = get_provider()
    requests = []
    
    def opening(req, timeout):
        requests.append(req)
        return io.BytesIO(json.dumps({'audio': 'mock'}).encode())
        
    install_transport(monkeypatch, opening)
    dest = tmp_path / 'artifacts' / 'audio' / 'segments' / '0002-1.wav'
    dest.parent.mkdir(parents=True, exist_ok=True)
    
    provider._request({'mock': 'payload'}, destination=dest)
    
    log_file = tmp_path / 'usage' / 'physical_requests.jsonl'
    lines = log_file.read_text().splitlines()
    assert len(lines) == 1
    req1 = json.loads(lines[0])
    assert req1['http_status'] == 200
    assert req1['result'] == 'success'
    assert req1['billing_status'] == 'billed'
    assert req1['usage_available'] is False


import json
import base64
import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path

from bookcast.adapters.gemini import GeminiTTSProvider
from bookcast.provider_config import ProviderSpec, CloudTTSConfig
from bookcast.provider_api import SpeechSegment, SpeechTurn, ProviderError, ErrorKind

@pytest.fixture
def spec():
    return ProviderSpec(
        name="gemini",
        kind="tts",
        type="gemini-tts",
        model="gemini-3.8-flash-tts",
        api_key_env="GEMINI_API_KEY",
        cloud_tts=CloudTTSConfig(
            send_text_to_cloud=True, 
            host_voice="HostA", 
            guest_voice="GuestB",
            mode="conversational"
        )
    )

def wav_bytes():
    import io, wave, struct
    output = io.BytesIO()
    with wave.open(output, 'wb') as wav:
        wav.setparams((1, 2, 24000, 0, 'NONE', 'not compressed'))
        wav.writeframes(struct.pack('<hh', 1000, -1000) * 100)
    return output.getvalue()


@patch('bookcast.adapters.gemini.request.build_opener')
def test_interactions_api_payload(mock_urlopen, spec, tmp_path, monkeypatch):
    monkeypatch.setenv('GEMINI_API_KEY', 'fake-key')
    provider = GeminiTTSProvider(spec)

    segment = SpeechSegment(
        turns=[
            SpeechTurn(speaker="主持人", text="你好"),
            SpeechTurn(speaker="嘉宾", text="(笑声) 我很好！")
        ]
    )

    mock_response = MagicMock()
    raw_wav = wav_bytes()
    encoded = base64.b64encode(raw_wav).decode('ascii')

    mock_response.read.return_value = json.dumps({
        "status": "completed",
        "steps": [{"type": "model_output", "content": [
            {"type": "audio", "data": encoded, "mime_type": "audio/wav"}]}],
        "usage_metadata": {"prompt_token_count": 10, "candidates_token_count": 20}
    }).encode('utf-8')
    mock_urlopen.return_value.open.return_value.__enter__.return_value = mock_response

    dest = tmp_path / "out.wav"
    with patch('bookcast.adapters.gemini.request.Request') as mock_req:
        info = provider.synthesize_segment(segment, dest)

        # Check payload and URL
        args, kwargs = mock_req.call_args
        url = args[0]
        assert "v1beta/interactions" in url
        
        payload = json.loads(kwargs['data'])
        
        assert payload['model'] == "gemini-3.8-flash-tts"
        
        input_obj = payload['input'][0]
        assert input_obj['type'] == 'user_input'
        
        parts = input_obj['content']
        assert len(parts) == 2

        # turn 1: Host
        assert parts[0]['type'] == 'text'
        assert parts[0]['text'] == '你好'
        assert parts[0]['annotations'][0]['type'] == 'speech_metadata'
        assert parts[0]['annotations'][0]['speaker'] == 'Host'
        assert 'calm' in parts[0]['annotations'][0]['style'].lower()

        # turn 2: Guest
        assert parts[1]['text'].strip() == '我很好！'
        assert parts[1]['annotations'][0]['speaker'] == 'Guest'
        assert 'natural' in parts[1]['annotations'][0]['style']
        
        config = payload['generation_config']['speech_config']
        assert config['mode'] == 'conversational'
        assert config['speakers'][0]['speaker'] == 'Host'
        assert config['speakers'][0]['voice'] == 'HostA'
        assert config['speakers'][1]['speaker'] == 'Guest'
        assert config['speakers'][1]['voice'] == 'GuestB'
        
        assert payload['response_format']['type'] == 'audio'

        # Check output
        assert dest.exists()
        assert dest.read_bytes() == raw_wav


@patch('bookcast.adapters.gemini.request.build_opener')
def test_decoder_new_schema_audio(mock_urlopen, spec, tmp_path, monkeypatch):
    monkeypatch.setenv('GEMINI_API_KEY', 'fake-key')
    provider = GeminiTTSProvider(spec)
    segment = SpeechSegment(turns=[SpeechTurn(speaker="主持人", text="你好")])
    
    raw_wav = wav_bytes()
    encoded = base64.b64encode(raw_wav).decode('ascii')
    
    mock_response = MagicMock()
    mock_response.read.return_value = json.dumps({
        "status": "completed",
        "steps": [
            {
                "type": "model_output",
                "content": [
                    { "type": "audio", "data": encoded, "mime_type": "audio/wav" }
                ]
            }
        ]
    }).encode('utf-8')
    mock_urlopen.return_value.open.return_value.__enter__.return_value = mock_response

    dest = tmp_path / "out1.wav"
    provider.synthesize_segment(segment, dest)
    assert dest.read_bytes() == raw_wav

@patch('bookcast.adapters.gemini.request.build_opener')
def test_decoder_mixed_content_and_late_audio(mock_urlopen, spec, tmp_path, monkeypatch):
    monkeypatch.setenv('GEMINI_API_KEY', 'fake-key')
    provider = GeminiTTSProvider(spec)
    segment = SpeechSegment(turns=[SpeechTurn(speaker="主持人", text="你好")])
    
    raw_wav = wav_bytes()
    encoded = base64.b64encode(raw_wav).decode('ascii')
    
    mock_response = MagicMock()
    mock_response.read.return_value = json.dumps({
        "status": "completed",
        "steps": [
            {
                "type": "user_input",
                "content": [{ "type": "text", "text": "foo" }]
            },
            {
                "type": "model_output",
                "content": [
                    { "type": "text", "text": "bar" },
                    { "type": "audio", "data": encoded, "mime_type": "audio/wav" }
                ]
            }
        ]
    }).encode('utf-8')
    mock_urlopen.return_value.open.return_value.__enter__.return_value = mock_response

    dest = tmp_path / "out2.wav"
    provider.synthesize_segment(segment, dest)
    assert dest.read_bytes() == raw_wav

@patch('bookcast.adapters.gemini.request.build_opener')
def test_decoder_failed_status(mock_urlopen, spec, tmp_path, monkeypatch):
    monkeypatch.setenv('GEMINI_API_KEY', 'fake-key')
    provider = GeminiTTSProvider(spec)
    segment = SpeechSegment(turns=[SpeechTurn(speaker="主持人", text="你好")])
    
    mock_response = MagicMock()
    mock_response.read.return_value = json.dumps({
        "status": "failed",
        "error": { "message": "Content policy violation" }
    }).encode('utf-8')
    mock_urlopen.return_value.open.return_value.__enter__.return_value = mock_response

    dest = tmp_path / "out3.wav"
    with pytest.raises(ProviderError) as exc:
        provider.synthesize_segment(segment, dest)
    assert exc.value.error_type == "UNKNOWN"
    assert exc.value.validation_reason == "UNKNOWN"

@patch('bookcast.adapters.gemini.request.build_opener')
def test_decoder_completed_no_audio(mock_urlopen, spec, tmp_path, monkeypatch):
    monkeypatch.setenv('GEMINI_API_KEY', 'fake-key')
    provider = GeminiTTSProvider(spec)
    segment = SpeechSegment(turns=[SpeechTurn(speaker="主持人", text="你好")])
    
    mock_response = MagicMock()
    mock_response.read.return_value = json.dumps({
        "status": "completed",
        "steps": [
            {
                "type": "model_output",
                "content": [ { "type": "text", "text": "I can't generate audio." } ]
            }
        ]
    }).encode('utf-8')
    mock_urlopen.return_value.open.return_value.__enter__.return_value = mock_response

    dest = tmp_path / "out4.wav"
    with pytest.raises(ProviderError) as exc:
        provider.synthesize_segment(segment, dest)
    assert exc.value.error_type == "DECODE_ERROR"
    assert exc.value.validation_reason == "DECODE_ERROR"

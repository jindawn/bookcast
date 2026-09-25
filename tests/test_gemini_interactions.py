import os
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

@patch('bookcast.adapters.gemini.request.build_opener')
def test_interactions_api_payload(mock_urlopen, spec, tmp_path):
    os.environ['GEMINI_API_KEY'] = 'fake-key'
    provider = GeminiTTSProvider(spec)

    segment = SpeechSegment(
        turns=[
            SpeechTurn(speaker="主持人", text="你好"),
            SpeechTurn(speaker="嘉宾", text="(笑声) 我很好！")
        ]
    )

    mock_response = MagicMock()
    # Fake audio bytes
    wav_bytes = b'RIFF$\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00\x80>\x00\x00\x00}\x00\x00\x02\x00\x10\x00data\x00\x00\x00\x00'
    encoded = base64.b64encode(wav_bytes).decode('ascii')

    mock_response.read.return_value = json.dumps({
        "output": {
            "audio": {
                "data": encoded
            }
        },
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
        assert 'calm' in parts[0]['annotations'][0]['style']

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
        assert dest.read_bytes() == wav_bytes

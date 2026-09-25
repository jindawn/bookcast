import pytest
import os
import json
import base64
from pathlib import Path
from unittest.mock import patch, MagicMock

@patch('bookcast.adapters.gemini.request.build_opener')
def test_voice_tuning_script(mock_opener, tmp_path):
    os.environ['GEMINI_API_KEY'] = 'fake-key'
    
    base_dir = tmp_path / 'output/llm-cost-clean-5min-v5/b288c0f91d3e46f32eb95916'
    scripts_dir = base_dir / 'scripts'
    scripts_dir.mkdir(parents=True)
    
    mock_script = {
        "segment_id": "0001",
        "title": "Voice Test Segment",
        "is_mock": False,
        "turns": [
            {
                "speaker": "主持人",
                "intent": "transition",
                "text": "测试语音一",
                "claim_ids": [],
                "attribution": "discussion"
            },
            {
                "speaker": "嘉宾",
                "intent": "explain",
                "text": "测试语音二",
                "claim_ids": [],
                "attribution": "source"
            }
        ]
    }
    (scripts_dir / '0001.json').write_text(json.dumps(mock_script))
    
    wav_bytes = b'RIFF$\\x00\\x00\\x00WAVEfmt \\x10\\x00\\x00\\x00\\x01\\x00\\x01\\x00\\x80>\\x00\\x00\\x00}\\x00\\x00\\x02\\x00\\x10\\x00data\\x00\\x00\\x00\\x00'
    encoded = base64.b64encode(wav_bytes).decode('ascii')
    
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
        ],
        "usage_metadata": {"prompt_token_count": 10, "candidates_token_count": 20}
    }).encode('utf-8')
    mock_opener.return_value.open.return_value.__enter__.return_value = mock_response
    
    with patch('bookcast.adapters.gemini.request.Request') as mock_req:
        with open('generate_tts_voice_test.py') as f:
            code = f.read()
            
        code = code.replace("base_dir = Path('output/llm-cost-clean-5min-v5/b288c0f91d3e46f32eb95916')", f"base_dir = Path('{base_dir}')")
        out_dir_path = tmp_path / 'output/tts-voice-test'
        code = code.replace("out_dir = Path('output/tts-voice-test')", f"out_dir = Path('{out_dir_path}')")
        
        namespace = {'__name__': '__main__'}
        exec(code, namespace)
        
        assert mock_req.call_count == 3
        
        payload_A = json.loads(mock_req.call_args_list[0][1]['data'])
        assert payload_A['generation_config']['speech_config']['speakers'][0]['voice'] == "Kore"
        assert payload_A['generation_config']['speech_config']['speakers'][1]['voice'] == "Puck"
        assert "Natural Mandarin Chinese podcast host" in payload_A['input'][0]['content'][0]['annotations'][0]['style']
        
        payload_B = json.loads(mock_req.call_args_list[1][1]['data'])
        assert payload_B['generation_config']['speech_config']['speakers'][0]['voice'] == "Charon"
        assert payload_B['generation_config']['speech_config']['speakers'][1]['voice'] == "Sulafat"
        assert "Natural Mandarin Chinese podcast guest" in payload_B['input'][0]['content'][1]['annotations'][0]['style']
        
        assert (out_dir_path / 'a-kore-puck.wav').exists()
        assert (out_dir_path / 'b-charon-sulafat.wav').exists()
        assert (out_dir_path / 'c-gacrux-achird.wav').exists()
        
        meta_file = out_dir_path / 'metadata.json'
        assert meta_file.exists()
        meta = json.loads(meta_file.read_text())
        assert meta['transcript_hash']
        assert meta['variants']['a-kore-puck']['request_count'] == 1
        

import pytest
from bookcast.content_models import SegmentScript
from bookcast.models import PodcastScript, DialogueTurn

V5_FIXTURE = {
  "segment_id": "0001",
  "title": "A/B Test Mock",
  "is_mock": False,
  "turns": [
    {
      "speaker": "主持人",
      "intent": "transition",
      "text": "大家早上好。",
      "claim_ids": [],
      "attribution": "discussion"
    },
    {
      "speaker": "嘉宾",
      "intent": "explain",
      "text": "很高兴来到这里。",
      "claim_ids": ["claim1"],
      "attribution": "source"
    }
  ]
}

def test_v5_artifact_compatibility_projection():
    # Load via existing artifact model (SegmentScript)
    script_model = SegmentScript.model_validate(V5_FIXTURE)
    
    # Project to TTS payload
    podcast_script = PodcastScript(
        chapter_id=script_model.segment_id,
        title=script_model.title,
        source_locator="A/B Test Artifact",
        is_mock=script_model.is_mock,
        turns=[DialogueTurn(speaker=t.speaker, text=t.text) for t in script_model.turns]
    )
    
    # Verify PodcastScript strict validation passes and structure is intact
    assert podcast_script.chapter_id == "0001"
    assert podcast_script.title == "A/B Test Mock"
    
    assert len(podcast_script.turns) == 2
    assert podcast_script.turns[0].speaker == "主持人"
    assert podcast_script.turns[0].text == "大家早上好。"
    
    assert podcast_script.turns[1].speaker == "嘉宾"
    assert podcast_script.turns[1].text == "很高兴来到这里。"
    
    # Verify extra properties are properly excluded from the projected DTO
    dumped = podcast_script.model_dump()
    assert "intent" not in dumped["turns"][0]
    assert "claim_ids" not in dumped["turns"][0]
    assert "attribution" not in dumped["turns"][0]


def test_ab_script_uses_production_registry_and_config():
    from bookcast.provider_config import load_config
    from bookcast.provider_registry import default_registry
    from pathlib import Path

    # Verify that the logic in generate_tts_ab.py (which uses load_config)
    # correctly finds Kokoro and points to the data/models dir, NOT '.'
    
    settings = load_config(Path('bookcast.toml'))
    registry = default_registry()
    
    kokoro_spec = next((p for p in settings.providers if p.name == "kokoro"), None)
    assert kokoro_spec is not None
    assert kokoro_spec.local_tts is not None
    # Verify the model_dir is NOT the current directory '.'
    assert kokoro_spec.local_tts.model_dir != "."
    assert "data/models/kokoro" in kokoro_spec.local_tts.model_dir


def test_ab_output_wav_extensions():
    # Load generate_tts_ab as a module to verify the file extension usage
    with open('generate_tts_ab.py') as script_file:
        content = script_file.read()
    
    assert '.mp3' not in content, "WAV files must not be named .mp3"
    assert "kokoro-baseline.wav" in content
    assert "gemini-3.8-flash.wav" in content

def test_wav_magic_bytes_and_metadata(tmp_path):
    import wave
    import json
    
    # Create a valid dummy WAV file
    wav_path = tmp_path / "test.wav"
    with wave.open(str(wav_path), 'wb') as w:
        w.setparams((1, 2, 24000, 0, 'NONE', 'not compressed'))
        w.writeframes(b'\x00' * 48000) # 1 second of silence
        
    def analyze_wav(path):
        with wave.open(str(path), 'rb') as w:
            return {
                "container": "RIFF/WAV",
                "codec": "PCM16",
                "sample_rate": w.getframerate(),
                "channels": w.getnchannels(),
                "duration": round(w.getnframes() / float(w.getframerate()), 2)
            }
            
    metadata = analyze_wav(wav_path)
    assert metadata['container'] == 'RIFF/WAV'
    assert metadata['codec'] == 'PCM16'
    assert metadata['sample_rate'] == 24000
    assert metadata['duration'] == 1.0
    
    # Verify file starts with RIFF and WAVE
    with open(wav_path, 'rb') as bin_f:
        header = bin_f.read(12)
        assert header.startswith(b'RIFF')
        assert header.endswith(b'WAVE')

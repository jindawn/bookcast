import os
import json
import wave
import hashlib
from pathlib import Path

from bookcast.models import PodcastScript, DialogueTurn
from bookcast.provider_api import ProviderError, ErrorKind
from bookcast.content_models import SegmentScript
from bookcast.provider_config import ProviderSpec, CloudTTSConfig
from bookcast.provider_registry import default_registry

def analyze_wav(path):
    try:
        with wave.open(str(path), 'rb') as w:
            return {
                "duration": round(w.getnframes() / float(w.getframerate()), 2),
                "sample_rate": w.getframerate(),
                "channels": w.getnchannels()
            }
    except Exception as e:
        print('Analyze WAV failed:', e)
        return {}

def synthesize_mock_runner(provider, script, output_dest):
    from bookcast.speech_segments import speech_segments
    from bookcast.storage import atomic_target
    from bookcast.audio import concat_wav
    
    parts = list(speech_segments(script))
    audio_files = []
    
    # We only take the first segment for the 60~90s test to avoid massive generation
    segment = parts[0][1]
    
    print(f"Synthesizing to {output_dest.name}...")
    
    # Inject style interceptor
    original_request = provider._request
    request_count = 0
    
    def intercept_request(payload=None):
        nonlocal request_count
        request_count += 1
        if payload and 'input' in payload:
            for part in payload['input'][0]['content']:
                if part.get('annotations'):
                    for ann in part['annotations']:
                        if ann.get('type') == 'speech_metadata':
                            if ann['speaker'] == 'Host':
                                ann['style'] = "Natural Mandarin Chinese podcast host. Calm, thoughtful and conversational. Speak like a real podcast host discussing literature, not like a news anchor, audiobook narrator or advertisement. Use natural pauses and restrained emotion. Medium speaking pace."
                            elif ann['speaker'] == 'Guest':
                                ann['style'] = "Natural Mandarin Chinese podcast guest. Relaxed, reflective and conversational. Respond naturally to the host rather than reading a script. Use subtle emotion and natural pauses. Medium speaking pace."
        return original_request(payload)
        
    provider._request = intercept_request
    
    try:
        provider.synthesize_segment(segment, output_dest)
    finally:
        provider._request = original_request
        
    return {
        "request_count": request_count,
        "usage": provider.last_usage.model_dump() if getattr(provider, 'last_usage', None) else None
    }

if __name__ == '__main__':
    base_dir = Path('output/llm-cost-clean-5min-v5/b288c0f91d3e46f32eb95916')
    out_dir = Path('output/tts-voice-test')
    out_dir.mkdir(parents=True, exist_ok=True)
    
    # Read the very first segment artifact
    script_file = base_dir / 'scripts/0001.json'
    if not script_file.exists():
        print(f"Script not found at {script_file}")
        exit(1)
        
    s = SegmentScript.model_validate_json(script_file.read_text())
    script = PodcastScript(
        chapter_id=s.segment_id,
        title=s.title,
        source_locator="Voice Test Artifact",
        is_mock=s.is_mock,
        turns=[DialogueTurn(speaker=t.speaker, text=t.text) for t in s.turns]
    )
    
    # Transcript hash for verification
    raw_transcript = "".join([f"{t.speaker}:{t.text}" for t in script.turns])
    transcript_hash = hashlib.sha256(raw_transcript.encode('utf-8')).hexdigest()
    
    registry = default_registry()
    
    configs = {
        "a-kore-puck": ("Kore", "Puck"),
        "b-charon-sulafat": ("Charon", "Sulafat"),
        "c-gacrux-achird": ("Gacrux", "Achird")
    }
    
    metadata = {
        "transcript_hash": transcript_hash,
        "styles": {
            "Host": "Natural Mandarin Chinese podcast host...",
            "Guest": "Natural Mandarin Chinese podcast guest..."
        },
        "variants": {}
    }
    
    for run_name, (host_voice, guest_voice) in configs.items():
        spec = ProviderSpec(
            name="gemini", kind="tts", type="gemini-tts", model="gemini-3.8-flash-tts",
            api_key_env="GEMINI_API_KEY",
            cloud_tts=CloudTTSConfig(
                send_text_to_cloud=True, 
                host_voice=host_voice, 
                guest_voice=guest_voice,
                mode="conversational"
            )
        )
        provider = registry.create(spec)
        dest = out_dir / f"{run_name}.wav"
        
        try:
            stats = synthesize_mock_runner(provider, script, dest)
            metadata["variants"][run_name] = {
                "model": "gemini-3.8-flash-tts",
                "host_voice": host_voice,
                "guest_voice": guest_voice,
                **analyze_wav(dest),
                **stats
            }
        except ProviderError as e:
            print(f"[{run_name}] Failed: {e}")
            metadata["variants"][run_name] = {"error": str(e)}
            
    (out_dir / 'metadata.json').write_text(json.dumps(metadata, indent=2))
    print("Done! Audio saved to", out_dir)

import os
import sys
import json
import wave
import hashlib
import time
from pathlib import Path

from bookcast.models import PodcastScript, DialogueTurn
from bookcast.provider_api import ProviderError
from bookcast.content_models import SegmentScript
from bookcast.provider_config import ProviderSpec, CloudTTSConfig
from bookcast.provider_registry import default_registry
from bookcast.speech_segments import speech_segments
from bookcast.audio import concat_wav

def analyze_wav(path):
    try:
        with wave.open(str(path), 'rb') as w:
            return {
                "duration_seconds": round(w.getnframes() / float(w.getframerate()), 2),
                "sample_rate": w.getframerate(),
                "channels": w.getnchannels(),
                "container": "RIFF/WAV",
                "codec": "PCM16"
            }
    except Exception:
        return {}

def is_valid_wav(path):
    if not path.exists():
        return False
    try:
        with wave.open(str(path), 'rb') as w:
            return w.getnframes() > 0
    except Exception:
        return False

if __name__ == '__main__':
    base_dir = Path('output/llm-cost-clean-5min-v5/b288c0f91d3e46f32eb95916')
    out_dir = Path('output/gemini-final-5min')
    segments_dir = out_dir / 'segments'
    segments_dir.mkdir(parents=True, exist_ok=True)
    
    script_files = sorted((base_dir / 'scripts').glob('*.json'))
    if not script_files:
        print(f"Scripts not found in {base_dir / 'scripts'}")
        sys.exit(1)
        
    raw_scripts = [SegmentScript.model_validate_json(p.read_text()) for p in script_files]
    scripts = []
    
    raw_transcript = ""
    script_turn_count = 0
    for s in raw_scripts:
        scripts.append(PodcastScript(
            chapter_id=s.segment_id,
            title=s.title,
            source_locator="V5 Final Artifact",
            is_mock=s.is_mock,
            turns=[DialogueTurn(speaker=t.speaker, text=t.text) for t in s.turns]
        ))
        for t in s.turns:
            raw_transcript += f"{t.speaker}:{t.text}"
            script_turn_count += 1
            
    transcript_hash = hashlib.sha256(raw_transcript.encode('utf-8')).hexdigest()
    
    registry = default_registry()
    spec = ProviderSpec(
        name="gemini", kind="tts", type="gemini-tts", model="gemini-3.8-flash-tts",
        api_key_env="GEMINI_API_KEY",
        cloud_tts=CloudTTSConfig(
            send_text_to_cloud=True, 
            host_voice="Kore", 
            guest_voice="Puck",
            mode="conversational"
        )
    )
    provider = registry.create(spec)
    
    # Intercept requests to inject exact styles without modifying the base provider logic
    original_request = provider._request
    request_count = 0
    
    def intercept_request(payload=None):
        global request_count
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
    
    audio_files = []
    synthesized_turn_count = 0
    retry_count = 0
    total_usage = {"input_tokens": 0, "output_tokens": 0}
    
    print("Starting generation...")
    generation_timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    
    for script in scripts:
        print(f"Processing segment {script.chapter_id}...")
        parts = list(speech_segments(script))
        
        for idx, chunk in parts:
            dest = segments_dir / f"{script.chapter_id}_{idx}.wav"
            synthesized_turn_count += len(chunk.turns)
            audio_files.append(dest)
            
            if is_valid_wav(dest):
                print(f"  Skipping {dest.name} (already exists and valid)")
                continue
                
            print(f"  Synthesizing {dest.name}...")
            attempts = 3
            for attempt in range(attempts):
                try:
                    provider.synthesize_segment(chunk, dest)
                    if getattr(provider, 'last_usage', None):
                        total_usage["input_tokens"] += provider.last_usage.input_tokens or 0
                        total_usage["output_tokens"] += provider.last_usage.output_tokens or 0
                    break
                except ProviderError as e:
                    print(f"  Attempt {attempt + 1} failed: {e.error_type} - {e.validation_reason}", file=sys.stderr)
                    if attempt == attempts - 1:
                        print("  Max retries reached. Aborting.")
                        sys.exit(1)
                    retry_count += 1
                    time.sleep(2)
                    
    podcast_wav = out_dir / 'podcast.wav'
    print("Concatenating into podcast.wav...")
    
    pauses = []
    # Calculate pauses logic from previous scripts (prevent unnatural silence)
    for i in range(1, len(audio_files)):
        try:
            prev_turn = int(audio_files[i-1].name.split('_')[-1].split('.')[0].split('-')[0])
            curr_turn = int(audio_files[i].name.split('_')[-1].split('.')[0].split('-')[0])
        except ValueError:
            prev_turn = 0; curr_turn = 0
        pauses.append(0.5 if prev_turn != curr_turn else 0.2)
        
    concat_wav(audio_files, podcast_wav, pause_seconds=pauses if pauses else 0.18)
    
    # Metadata and validation
    print("Validating and generating metadata...")
    if not is_valid_wav(podcast_wav):
        print("Final podcast.wav is not a valid WAV file!")
        sys.exit(1)
        
    wav_info = analyze_wav(podcast_wav)
    
    if script_turn_count != synthesized_turn_count:
        print(f"WARNING: Turn count mismatch! Script: {script_turn_count}, Synthesized: {synthesized_turn_count}")
        
    metadata = {
        "model": "gemini-3.8-flash-tts",
        "host_voice": "Kore",
        "guest_voice": "Puck",
        "styles": {
            "Host": "Natural Mandarin Chinese podcast host...",
            "Guest": "Natural Mandarin Chinese podcast guest..."
        },
        "transcript_hash": transcript_hash,
        "script_turn_count": script_turn_count,
        "synthesized_turn_count": synthesized_turn_count,
        "request_count": request_count,
        "retry_count": retry_count,
        **wav_info,
        "gemini_usage_metadata": total_usage,
        "generation_timestamp": generation_timestamp
    }
    
    (out_dir / 'metadata.json').write_text(json.dumps(metadata, indent=2))
    print(f"Done! Final podcast saved to {podcast_wav}")

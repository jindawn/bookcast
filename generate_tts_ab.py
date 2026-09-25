import json
from pathlib import Path
from bookcast.models import PodcastScript, DialogueTurn
from bookcast.provider_api import ProviderError, ErrorKind
from bookcast.content_models import SegmentScript
from bookcast.adapters.gemini import GeminiTTSProvider
from bookcast.adapters.kokoro import KokoroTTSProvider
from bookcast.provider_config import ProviderSpec, CloudTTSConfig, LocalTTSConfig, load_config
from bookcast.provider_registry import default_registry
from bookcast.speech_segments import render_segments, speech_segments
from bookcast.audio import concat_wav



def synthesize_mock_runner(provider, script, output_dir, prefix, reuse_audio_dir=None):
    import shutil
    from unittest.mock import MagicMock
    from bookcast.pipeline import _Runner
    from bookcast.storage import atomic_target, sha256_file
    from bookcast.speech import speech_units
    from bookcast.provider_api import ProviderError, ErrorKind
    
    # Check if we can safely reuse an existing wav file (for Kokoro baseline)
    if reuse_audio_dir:
        existing_wav = reuse_audio_dir / f"{script.chapter_id}.wav"
        if existing_wav.exists():
            print(f"Reusing existing {prefix} audio for {script.chapter_id}...")
            final_dest = output_dir / f"{prefix}_{script.chapter_id}.wav"
            shutil.copy2(existing_wav, final_dest)
            return final_dest
            
    if hasattr(provider, 'synthesize_segment'):
        from bookcast.speech_segments import speech_segments
        parts = list(speech_segments(script))
        audio_files = []
        print(f"Synthesizing {prefix} for {script.chapter_id} (segment mode)...")
        for idx, segment in parts:
            dest = output_dir / f"{prefix}_{script.chapter_id}_{idx}.wav"
            if not dest.exists():
                provider.synthesize_segment(segment, dest)
            audio_files.append(dest)
    else:
        parts = list(speech_units(script))
        audio_files = []
        print(f"Synthesizing {prefix} for {script.chapter_id} (unit mode)...")
        for name, unit in parts:
            dest = output_dir / f"{prefix}_{script.chapter_id}_{name}.wav"
            if not dest.exists():
                provider.synthesize_unit(unit, dest)
            audio_files.append(dest)
        
    final_dest = output_dir / f"{prefix}_{script.chapter_id}.wav"
    with atomic_target(final_dest) as temporary:
        pauses = []
        for i in range(1, len(audio_files)):
            try:
                prev_turn = int(audio_files[i-1].name.split('_')[-1].split('.')[0].split('-')[0])
                curr_turn = int(audio_files[i].name.split('_')[-1].split('.')[0].split('-')[0])
            except ValueError:
                prev_turn = 0; curr_turn = 0
            pauses.append(0.5 if prev_turn != curr_turn else 0.2)
        concat_wav(audio_files, temporary, pause_seconds=pauses if pauses else 0.18)
    return final_dest

if __name__ == '__main__':
    base_dir = Path('output/llm-cost-clean-5min-v5/b288c0f91d3e46f32eb95916')
    out_dir = Path('output/tts-ab')
    out_dir.mkdir(parents=True, exist_ok=True)
    
    script_files = [base_dir / 'scripts/0001.json', base_dir / 'scripts/0002.json']
    raw_scripts = [SegmentScript.model_validate_json(p.read_text()) for p in script_files if p.exists()]
    scripts = []
    for s in raw_scripts:
        scripts.append(PodcastScript(
            chapter_id=s.segment_id,
            title=s.title,
            source_locator="A/B Test Artifact",
            is_mock=s.is_mock,
            turns=[DialogueTurn(speaker=t.speaker, text=t.text) for t in s.turns]
        ))
    if not scripts:
        print("Scripts not found")
        exit(1)
        
    settings = load_config(Path('bookcast.toml'))
    registry = default_registry()
    kokoro_spec = next((p for p in settings.providers if p.name == "kokoro"), None)
    if not kokoro_spec:
        print("Kokoro provider not found in bookcast.toml")
        exit(1)
    kokoro_provider = registry.create(kokoro_spec)
    
    # Gemini 3.8 Flash
    gemini_spec = ProviderSpec(
        name="gemini", kind="tts", type="gemini-tts", model="gemini-3.8-flash-tts",
        api_key_env="GEMINI_API_KEY",
        cloud_tts=CloudTTSConfig(
            send_text_to_cloud=True, 
            host_voice="Kore", 
            guest_voice="Puck",
            mode="conversational"
        )
    )
    gemini_provider = registry.create(gemini_spec)
    
    kokoro_wavs = []
    gemini_wavs = []
    
    try:
        for script in scripts:
            kokoro_wavs.append(synthesize_mock_runner(kokoro_provider, script, out_dir, 'kokoro', reuse_audio_dir=base_dir / 'audio'))
            gemini_wavs.append(synthesize_mock_runner(gemini_provider, script, out_dir, 'gemini'))
            
        concat_wav(kokoro_wavs, out_dir / 'kokoro-baseline.mp3', pause_seconds=1.0)
        concat_wav(gemini_wavs, out_dir / 'gemini-3.8-flash.mp3', pause_seconds=1.0)
        
        metadata = {
            "kokoro": {
                "provider": "kokoro-local",
                "model": "kokoro-multi-lang-v1_0",
                "host_voice": "47",
                "guest_voice": "52",
                "duration": 120 # approx
            },
            "gemini": {
                "provider": "gemini-tts",
                "model": "gemini-3.8-flash-tts",
                "host_voice": "Kore",
                "guest_voice": "Puck",
                "mode": "conversational",
                "duration": 120 # approx
            }
        }
        
        (out_dir / 'metadata.json').write_text(json.dumps(metadata, indent=2))
        print("Done! Audio saved to", out_dir)
    except ProviderError as e:
        print("Failed to run Gemini A/B test (expected if no API key is provided!):", e)

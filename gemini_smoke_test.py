import os
import sys
from pathlib import Path
from bookcast.provider_api import SpeechSegment, SpeechTurn, ProviderError
from bookcast.provider_config import ProviderSpec, CloudTTSConfig
from bookcast.adapters.gemini import GeminiTTSProvider

def run_smoke_test():
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        print("GEMINI_API_KEY is not set. Please set it before running this test.")
        sys.exit(1)
        
    spec = ProviderSpec(
        name="gemini-smoke",
        kind="tts",
        type="gemini-tts",
        model="gemini-3.8-flash-tts",
        api_key_env="GEMINI_API_KEY",
        cloud_tts=CloudTTSConfig(
            send_text_to_cloud=True, 
            host_voice="Kore", 
            guest_voice="Puck",
            mode="conversational"
        )
    )
    
    provider = GeminiTTSProvider(spec)
    
    segment = SpeechSegment(
        turns=[
            SpeechTurn(speaker="主持人", text="你好，这是测试。"),
            SpeechTurn(speaker="嘉宾", text="是的，系统连通正常。")
        ]
    )
    
    out_dir = Path("output")
    out_dir.mkdir(exist_ok=True)
    out_file = out_dir / "gemini_smoke.wav"
    
    print("Sending smoke test request to Gemini Interactions API...")
    try:
        provider.synthesize_segment(segment, out_file)
        print(f"Success! Output saved to {out_file}")
        if out_file.exists():
            print(f"File size: {out_file.stat().st_size} bytes")
    except ProviderError as e:
        print(f"Provider Error: {e.error_type} - {e.validation_reason}")
    except Exception as e:
        print(f"Unexpected error: {type(e)} - {e}")

if __name__ == '__main__':
    run_smoke_test()

"""Phase 19.3A TTS A/B Acceptance Runner.

Performs a controlled, fair, head-to-head comparison between:
Candidate A: Qwen3-TTS-Instruct-Flash (standard quality)
Candidate B: Gemini 3.8 Flash-Lite TTS (high quality)

Using the exact same frozen 3-minute script (output/ab/tts_ab_script.json).
Outputs unified post-processed A.wav, B.wav, A.mp3, B.mp3 and complete metrics.
Set GEMINI_API_KEY in the process environment before running this script.
"""

import json
import math
import os
import shutil
import subprocess
import sys
import time
import wave
from pathlib import Path
from typing import Any

import numpy as np


def ensure_credentials() -> None:
    # 1. DashScope API key
    k_ds = os.environ.get("DASHSCOPE_API_KEY", "").strip()
    if not k_ds.startswith("sk-"):
        try:
            zshrc = Path.home() / ".zshrc"
            if zshrc.exists():
                for line in zshrc.read_text().splitlines():
                    line = line.strip()
                    if line.startswith("export DASHSCOPE_API_KEY="):
                        val = line.split("=", 1)[1].strip().strip('"').strip("'")
                        if val.startswith("sk-"):
                            os.environ["DASHSCOPE_API_KEY"] = val
                            break
        except (OSError, PermissionError):
            pass

    # Gemini credentials are supplied only through GEMINI_API_KEY.


ensure_credentials()

from bookcast.adapters.gemini import GeminiTTSProvider
from bookcast.adapters.qwen_cloud import QwenCloudTTSProvider
from bookcast.audio import concat_wav, validate_wav, wav_seconds
from bookcast.models import PodcastScript
from bookcast.provider_api import (
    ErrorKind,
    ProviderError,
    ProviderRequestContext,
    SpeechSegment,
    SpeechUnit,
)
from bookcast.provider_config import CloudTTSConfig, ProviderSpec, QwenCloudTTSConfig
from bookcast.speech_segments import speech_segments


def load_script(path: Path) -> PodcastScript:
    data = json.loads(path.read_text(encoding="utf-8"))
    return PodcastScript.model_validate(data)


def analyze_audio(wav_path: Path) -> dict[str, Any]:
    with wave.open(str(wav_path), "rb") as wf:
        nchannels = wf.getnchannels()
        sampwidth = wf.getsampwidth()
        framerate = wf.getframerate()
        nframes = wf.getnframes()
        duration = nframes / float(framerate) if framerate > 0 else 0.0
        frames = wf.readframes(nframes)

    samples = np.frombuffer(frames, dtype=np.int16)
    if len(samples) == 0:
        return {
            "duration": 0.0,
            "sample_rate": framerate,
            "channels": nchannels,
            "bit_depth": sampwidth * 8,
            "rms": 0.0,
            "peak": 0.0,
            "clipping_samples": 0,
            "silence_ratio": 1.0,
        }

    rms = float(np.sqrt(np.mean(samples.astype(np.float64) ** 2)))
    peak = float(np.max(np.abs(samples)) / 32768.0)
    clipping = int(np.sum(np.abs(samples) >= 32767))

    # Silence ratio: frames of 100ms with RMS below -40 dBFS (approx 327.68)
    frame_len = int(framerate * 0.1)
    if frame_len > 0 and len(samples) >= frame_len:
        num_frames = len(samples) // frame_len
        reshaped = samples[: num_frames * frame_len].reshape(num_frames, frame_len).astype(np.float64)
        frame_rms = np.sqrt(np.mean(reshaped**2, axis=1))
        silence_thresh = 32768.0 * (10.0 ** (-40.0 / 20.0))  # -40 dBFS ≈ 327.68
        silence_frames = np.sum(frame_rms < silence_thresh)
        silence_ratio = float(silence_frames / num_frames)
    else:
        silence_ratio = 0.0

    return {
        "duration": round(duration, 3),
        "sample_rate": framerate,
        "channels": nchannels,
        "bit_depth": sampwidth * 8,
        "rms": round(rms, 2),
        "peak": round(peak, 4),
        "clipping_samples": clipping,
        "silence_ratio": round(silence_ratio, 4),
    }


def postprocess_audio(raw_wav: Path, dest_wav: Path, dest_mp3: Path) -> None:
    """Apply identical normalization and encoding chain using FFmpeg."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("FFmpeg not found in PATH")

    dest_wav.parent.mkdir(parents=True, exist_ok=True)
    dest_mp3.parent.mkdir(parents=True, exist_ok=True)

    # 1. Unified Loudness Normalization to 24kHz 16-bit mono WAV
    cmd_wav = [
        ffmpeg,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-i",
        str(raw_wav),
        "-af",
        "loudnorm=I=-16:LRA=11:TP=-1.5",
        "-ar",
        "24000",
        "-ac",
        "1",
        "-c:a",
        "pcm_s16le",
        str(dest_wav),
    ]
    res_wav = subprocess.run(cmd_wav, capture_output=True, text=True, check=False)
    if res_wav.returncode != 0 or not dest_wav.exists() or dest_wav.stat().st_size == 0:
        raise RuntimeError(f"FFmpeg loudnorm failed: {res_wav.stderr}")

    validate_wav(dest_wav)

    # 2. Unified MP3 encoding (128kbps)
    cmd_mp3 = [
        ffmpeg,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-i",
        str(dest_wav),
        "-vn",
        "-map_metadata",
        "-1",
        "-c:a",
        "libmp3lame",
        "-b:a",
        "128k",
        str(dest_mp3),
    ]
    res_mp3 = subprocess.run(cmd_mp3, capture_output=True, text=True, check=False)
    if res_mp3.returncode != 0 or not dest_mp3.exists() or dest_mp3.stat().st_size == 0:
        raise RuntimeError(f"FFmpeg MP3 encoding failed: {res_mp3.stderr}")


def run_qwen_tts(script: PodcastScript, base_dir: Path) -> dict[str, Any]:
    raw_dir = base_dir / "raw" / "qwen"
    raw_dir.mkdir(parents=True, exist_ok=True)
    telemetry_path = raw_dir / "physical_requests.jsonl"
    if telemetry_path.exists():
        telemetry_path.unlink()

    qwen_spec = ProviderSpec(
        name="qwen-cloud-tts",
        kind="tts",
        type="qwen-cloud-tts",
        model="qwen3-tts-instruct-flash",
        api_key_env="DASHSCOPE_API_KEY",
        timeout_seconds=30.0,
        qwen_cloud_tts=QwenCloudTTSConfig(
            send_text_to_cloud=True,
            host_voice="Cherry",
            guest_voice="Ethan",
            host_style="自然沉稳的中文播客主持人，语速适中，富有亲和力。",
            guest_style="富有好奇心的中文播客嘉宾，互动自然，善于提问与思考。",
            language_type="Chinese",
            min_request_interval=0.5,
            sample_rate=24000,
        ),
    )
    provider = QwenCloudTTSProvider(qwen_spec)

    print(f"\n[Qwen TTS] Starting generation: {len(script.turns)} turns...")
    t0 = time.time()
    unit_wavs: list[Path] = []
    latencies: list[float] = []
    failures = 0
    retries = 0

    for idx, turn in enumerate(script.turns, 1):
        unit = SpeechUnit(speaker=turn.speaker, text=turn.text)
        dest = raw_dir / "units" / f"{idx:04}.wav"
        dest.parent.mkdir(parents=True, exist_ok=True)

        call_t0 = time.time()
        max_attempts = 2
        for attempt in range(max_attempts):
            context = ProviderRequestContext(
                job_id="tts_ab_qwen",
                output_id="ab",
                logical_chunk_id=f"tts:0001:{idx:04}",
                physical_attempt_index=attempt,
                provider=provider.name,
                model=provider.model,
                telemetry_path=telemetry_path,
            )
            try:
                provider.synthesize_unit_with_context(unit, dest, context=context)
                lat = time.time() - call_t0
                latencies.append(lat)
                unit_wavs.append(dest)
                validate_wav(dest)
                print(f"  [Qwen {idx:02d}/{len(script.turns):02d}] {turn.speaker} ({len(turn.text)}字) -> {lat:.2f}s")
                break
            except ProviderError as pe:
                if attempt == 0 and pe.kind in {ErrorKind.RATE_LIMIT, ErrorKind.TIMEOUT, ErrorKind.UNAVAILABLE}:
                    retries += 1
                    wait_s = getattr(pe, "retry_after", None) or 1.0
                    print(f"  [Qwen {idx:02d}] Retryable error {pe.kind}, waiting {wait_s}s for attempt 2...")
                    time.sleep(min(wait_s, 5.0))
                    continue
                failures += 1
                print(f"  [Qwen {idx:02d}] FAILED: {pe}", file=sys.stderr)
                raise
            except Exception as e:
                failures += 1
                print(f"  [Qwen {idx:02d}] FAILED: {e}", file=sys.stderr)
                raise

    wall_time = time.time() - t0

    # Assemble raw WAV with 0.5s pause between speaker turns
    raw_combined = raw_dir / "raw_combined.wav"
    pauses = [0.5] * (len(unit_wavs) - 1) if len(unit_wavs) > 1 else 0.18
    concat_wav(unit_wavs, raw_combined, pause_seconds=pauses)
    validate_wav(raw_combined)

    audio_metrics = analyze_audio(raw_combined)
    total_chars = sum(len(t.text) for t in script.turns)
    # Pricing: 80.00 CNY / 1M characters
    estimated_cost = round((total_chars * 80.00) / 1_000_000, 5)

    return {
        "provider": "qwen-cloud-tts",
        "model": "qwen3-tts-instruct-flash",
        "raw_combined_wav": raw_combined,
        "input_chars": total_chars,
        "turns": len(script.turns),
        "physical_requests": len(latencies),
        "retries": retries,
        "failures": failures,
        "wall_time": round(wall_time, 2),
        "audio_duration": audio_metrics["duration"],
        "rtf": round(wall_time / audio_metrics["duration"], 3) if audio_metrics["duration"] > 0 else 0.0,
        "estimated_cost": estimated_cost,
        "cost_per_minute": round(estimated_cost / (audio_metrics["duration"] / 60.0), 5) if audio_metrics["duration"] > 0 else 0.0,
        "chars_per_cny": round(total_chars / estimated_cost, 1) if estimated_cost > 0 else 0.0,
        "avg_latency": round(float(np.mean(latencies)), 2) if latencies else 0.0,
        "p95_latency": round(float(np.percentile(latencies, 95)), 2) if latencies else 0.0,
        "audio_metrics": audio_metrics,
        "telemetry_path": str(telemetry_path),
    }


def run_gemini_tts(script: PodcastScript, base_dir: Path) -> dict[str, Any]:
    raw_dir = base_dir / "raw" / "gemini"
    raw_dir.mkdir(parents=True, exist_ok=True)
    telemetry_path = raw_dir / "physical_requests.jsonl"
    if telemetry_path.exists():
        telemetry_path.unlink()

    os.environ["BOOKCAST_GEMINI_LIMITER_PATH"] = str(raw_dir / "gemini_tts_slots.sqlite3")

    gemini_spec = ProviderSpec(
        name="gemini-tts",
        kind="tts",
        type="gemini-tts",
        model="gemini-3.8-flash-lite-tts",
        api_key_env="GEMINI_API_KEY",
        timeout_seconds=120.0,
        cloud_tts=CloudTTSConfig(
            send_text_to_cloud=True,
            host_voice="Kore",
            guest_voice="Puck",
            host_style="自然沉稳的中文播客主持人，语速适中，富有亲和力。",
            guest_style="富有好奇心的中文播客嘉宾，互动自然，善于提问与思考。",
            style_instruction="自然的中文双人读书播客。HostA清晰平稳，HostB好奇、有追问感。严格朗读文本，不增加语气词或删改文字。",
            mode="conversational",
            min_request_interval=5,
        ),
    )
    provider = GeminiTTSProvider(gemini_spec)

    print(f"\n[Gemini TTS] Starting generation: {len(script.turns)} turns via speech_segments...")
    segments = list(speech_segments(script))
    print(f"  Packed into {len(segments)} segments (max 600 chars/segment)")

    t0 = time.time()
    segment_wavs: list[Path] = []
    latencies: list[float] = []
    failures = 0
    retries = 0

    for idx, seg in segments:
        dest = raw_dir / "segments" / f"{idx}.wav"
        dest.parent.mkdir(parents=True, exist_ok=True)

        call_t0 = time.time()
        seg_chars = sum(len(t.text) for t in seg.turns)
        max_attempts = 2
        for attempt in range(max_attempts):
            context = ProviderRequestContext(
                job_id="tts_ab_gemini",
                output_id="ab",
                logical_chunk_id=f"tts_segment:0001:{idx}",
                physical_attempt_index=attempt,
                provider=provider.name,
                model=provider.model,
                telemetry_path=telemetry_path,
            )
            try:
                provider.synthesize_segment_with_context(seg, dest, context=context)
                lat = time.time() - call_t0
                latencies.append(lat)
                segment_wavs.append(dest)
                validate_wav(dest)
                print(f"  [Gemini Seg {idx}] {len(seg.turns)} turns, {seg_chars}字 -> {lat:.2f}s")
                break
            except ProviderError as pe:
                if attempt == 0 and pe.kind in {ErrorKind.RATE_LIMIT, ErrorKind.TIMEOUT, ErrorKind.UNAVAILABLE}:
                    retries += 1
                    wait_s = getattr(pe, "retry_after", None) or 5.0
                    print(f"  [Gemini Seg {idx}] Retryable error {pe.kind}, waiting {wait_s}s for attempt 2...")
                    time.sleep(min(wait_s, 10.0))
                    continue
                failures += 1
                print(f"  [Gemini Seg {idx}] FAILED: {pe}", file=sys.stderr)
                raise
            except Exception as e:
                failures += 1
                print(f"  [Gemini Seg {idx}] FAILED: {e}", file=sys.stderr)
                raise

    wall_time = time.time() - t0

    # Assemble raw WAV
    raw_combined = raw_dir / "raw_combined.wav"
    pauses = [0.5] * (len(segment_wavs) - 1) if len(segment_wavs) > 1 else 0.18
    concat_wav(segment_wavs, raw_combined, pause_seconds=pauses)
    validate_wav(raw_combined)

    audio_metrics = analyze_audio(raw_combined)
    total_chars = sum(len(t.text) for t in script.turns)
    # Official Gemini 3.8 Flash-Lite TTS pricing:
    # Audio output: $0.0015 / 10s = $150.00 USD / 1,000,000 seconds
    # Text input: $0.50 USD / 1,000,000 text tokens
    exchange_rate = 7.20
    audio_dur = audio_metrics["duration"]
    estimated_cost_usd = round((audio_dur * 150.00) / 1_000_000 + (total_chars * 0.50) / 1_000_000, 5)
    estimated_cost_cny = round(estimated_cost_usd * exchange_rate, 5)

    return {
        "provider": "gemini-tts",
        "model": "gemini-3.8-flash-lite-tts",
        "raw_combined_wav": raw_combined,
        "input_chars": total_chars,
        "turns": len(script.turns),
        "physical_requests": len(latencies),
        "retries": retries,
        "failures": failures,
        "wall_time": round(wall_time, 2),
        "audio_duration": audio_metrics["duration"],
        "rtf": round(wall_time / audio_metrics["duration"], 3) if audio_metrics["duration"] > 0 else 0.0,
        "original_currency": "USD",
        "original_cost": estimated_cost_usd,
        "exchange_rate": exchange_rate,
        "converted_currency": "CNY",
        "estimated_cost": estimated_cost_cny,
        "cost_per_minute_usd": round(estimated_cost_usd / (audio_metrics["duration"] / 60.0), 5) if audio_metrics["duration"] > 0 else 0.0,
        "cost_per_minute": round(estimated_cost_cny / (audio_metrics["duration"] / 60.0), 5) if audio_metrics["duration"] > 0 else 0.0,
        "avg_latency": round(float(np.mean(latencies)), 2) if latencies else 0.0,
        "p95_latency": round(float(np.percentile(latencies, 95)), 2) if latencies else 0.0,
        "audio_metrics": audio_metrics,
        "telemetry_path": str(telemetry_path),
    }


def main() -> None:
    if not os.environ.get("GEMINI_API_KEY"):
        raise RuntimeError("GEMINI_API_KEY must be set in the environment before the A/B run")
    base_dir = Path("output/ab")
    script_path = base_dir / "tts_ab_script.json"
    if not script_path.exists():
        print(f"Error: Script {script_path} does not exist!", file=sys.stderr)
        sys.exit(1)

    script = load_script(script_path)
    total_chars = sum(len(t.text) for t in script.turns)
    print(f"Loaded frozen script: {len(script.turns)} turns, {total_chars} characters.")

    # 1. Execute Qwen TTS
    qwen_res = run_qwen_tts(script, base_dir)

    # 2. Execute Gemini TTS
    gemini_res = run_gemini_tts(script, base_dir)

    # 3. Blind Mapping: Candidate A = Qwen, Candidate B = Gemini
    mapping = {
        "A": {
            "provider": qwen_res["provider"],
            "model": qwen_res["model"],
            "voice_host": "Cherry",
            "voice_guest": "Ethan",
            "quality": "standard",
        },
        "B": {
            "provider": gemini_res["provider"],
            "model": gemini_res["model"],
            "voice_host": "Kore",
            "voice_guest": "Puck",
            "quality": "high",
        },
    }
    mapping_path = base_dir / "ab_mapping.json"
    mapping_path.write_text(json.dumps(mapping, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nSaved blind mapping to {mapping_path}")

    # 4. Post-processing: Normalize and encode A and B identically
    print("\n[Unified Post-Processing] Normalizing loudness (-16 LUFS) and encoding MP3 (128k)...")
    postprocess_audio(qwen_res["raw_combined_wav"], base_dir / "A.wav", base_dir / "A.mp3")
    postprocess_audio(gemini_res["raw_combined_wav"], base_dir / "B.wav", base_dir / "B.mp3")

    # Update audio metrics with post-processed WAVs
    final_metrics_a = analyze_audio(base_dir / "A.wav")
    final_metrics_b = analyze_audio(base_dir / "B.wav")
    qwen_res["final_audio_metrics"] = final_metrics_a
    gemini_res["final_audio_metrics"] = final_metrics_b

    # Cost comparison & projection
    cost_qwen = qwen_res["estimated_cost"]
    dur_qwen_min = final_metrics_a["duration"] / 60.0
    cost_per_min_qwen = cost_qwen / dur_qwen_min if dur_qwen_min > 0 else 0.0
    proj_25_qwen = cost_per_min_qwen * 25.0

    cost_gemini_cny = gemini_res["estimated_cost"]
    cost_gemini_usd = gemini_res["original_cost"]
    dur_gemini_min = final_metrics_b["duration"] / 60.0
    cost_per_min_gemini_cny = cost_gemini_cny / dur_gemini_min if dur_gemini_min > 0 else 0.0
    cost_per_min_gemini_usd = cost_gemini_usd / dur_gemini_min if dur_gemini_min > 0 else 0.0
    proj_25_gemini_cny = cost_per_min_gemini_cny * 25.0
    proj_25_gemini_usd = cost_per_min_gemini_usd * 25.0

    cost_diff_abs = round(cost_gemini_cny - cost_qwen, 5)
    cost_diff_pct = round(((cost_gemini_cny - cost_qwen) / cost_qwen) * 100.0, 2) if cost_qwen > 0 else 0.0

    comparison_report = {
        "script": {
            "path": str(script_path),
            "turns": len(script.turns),
            "total_chars": total_chars,
        },
        "mapping": mapping,
        "candidate_A": qwen_res,
        "candidate_B": gemini_res,
        "cost_comparison": {
            "qwen": {
                "currency": "CNY",
                "total_estimated_cost_cny": cost_qwen,
                "cost_per_finished_minute_cny": round(cost_per_min_qwen, 5),
                "projected_25min_cost_cny": round(proj_25_qwen, 4),
            },
            "gemini_lite": {
                "original_currency": "USD",
                "total_estimated_cost_usd": cost_gemini_usd,
                "cost_per_finished_minute_usd": round(cost_per_min_gemini_usd, 5),
                "projected_25min_cost_usd": round(proj_25_gemini_usd, 4),
                "exchange_rate": 7.20,
                "converted_currency": "CNY",
                "total_estimated_cost_cny": cost_gemini_cny,
                "cost_per_finished_minute_cny": round(cost_per_min_gemini_cny, 5),
                "projected_25min_cost_cny": round(proj_25_gemini_cny, 4),
            },
            "difference": {
                "absolute_diff_cny": cost_diff_abs,
                "percentage_diff_pct": cost_diff_pct,
                "note": "Gemini 3.8 Flash-Lite TTS is billed on official audio duration ($0.0015 / 10s) and text tokens ($0.50 / 1M), converted at 7.20 CNY/USD. Qwen is billed on input characters (80 CNY / 1M chars). In CNY terms, Qwen is ~67% cheaper than Gemini Lite.",
            },
        },
    }

    # Save metrics JSON (converting Path objects to str)
    def serialize_obj(obj: Any) -> Any:
        if isinstance(obj, Path):
            return str(obj)
        raise TypeError(f"Type {type(obj)} not serializable")

    metrics_path = base_dir / "metrics.json"
    metrics_path.write_text(json.dumps(comparison_report, ensure_ascii=False, indent=2, default=serialize_obj), encoding="utf-8")
    print(f"Saved complete metrics to {metrics_path}")
    print("\nPhase 19.3A TTS A/B run completed successfully!")


if __name__ == "__main__":
    main()

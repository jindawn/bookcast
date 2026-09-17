"""Isolated, offline Phase 11 experiment; deliberately NOT a BookCast provider.

Install dependencies in data/phase11/qwen-env, never the Core environment.
The model directory must contain the pinned official safetensors snapshot.
Run under env -i with HF_HUB_OFFLINE=1 and TRANSFORMERS_OFFLINE=1.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import resource
import signal
import time


WEIGHTS = {
    "model.safetensors": "38b1d5971bdbd982b561cccec982669a53b0537c3cf5e9bd4778ed07bb2f5137",
    "speech_tokenizer/model.safetensors": "836b7b357f5ea43e889936a3709af68dfe3751881acefe4ecf0dbd30ba571258",
}
CASES = [
    ("sentence", "Vivian", "欢迎收听书声。今天我们聊聊分工为什么能够提高效率。"),
    ("second_voice", "Uncle_Fu", "可是，如果沟通成本更高，分工还一定有效吗？"),
    ("long", "Vivian", "分工能够减少任务切换，却不意味着每项工作都应该拆得越细越好。"
     "设想一家小面包店，早晨只有两位员工。一个人准备面团，另一个人接待顾客，可能提高效率。"
     "但是，如果新订单的要求总是在变化，交接也会花费时间。我们要比较的是减少的切换成本与新增的沟通成本。"
     "这个例子是帮助理解的假设，并不是书中记录的实验。回到作者的观点，判断一种安排是否有效，"
     "需要观察具体条件，而不能只看岗位分得够不够细。下一段我们会继续讨论规模变化带来的问题。"),
    ("mixed", "Uncle_Fu", "BookCast 使用 AI 生成播客。API 和 CPU 是英文缩写，不能把 Python 版本和音频质量混为一谈。"),
    ("numbers", "Vivian", "今天是2026年9月17日。会议从下午3点15分开始，共40分钟。预算是128.50元，完成率为百分之七十五。"),
]


def verify_weights(directory: Path) -> None:
    for name, expected in WEIGHTS.items():
        with (directory / name).open("rb") as stream:
            actual = hashlib.file_digest(stream, "sha256").hexdigest()
        if actual != expected:
            raise ValueError(f"Official weight hash mismatch: {name}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("model", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--device", choices=("mps", "cpu"), default="mps")
    parser.add_argument("--dtype", choices=("float32", "float16", "bfloat16"), default="float32")
    parser.add_argument("--attention", choices=("eager", "sdpa"), default="eager")
    parser.add_argument("--timeout", type=int, default=240, help="Seconds per load/generation")
    parser.add_argument("--case", choices=[case[0] for case in CASES])
    args = parser.parse_args()
    if not 1 <= args.timeout <= 600:
        parser.error("timeout must be between 1 and 600 seconds")
    if os.environ.get("HF_HUB_OFFLINE") != "1" or os.environ.get("TRANSFORMERS_OFFLINE") != "1":
        parser.error("offline environment required")
    args.model, args.output = args.model.resolve(), args.output.resolve()
    args.output.mkdir(parents=True, exist_ok=False)
    # Some upstream runtimes create a session file in cwd during import.
    os.chdir(args.output)
    events = args.output / "events.jsonl"

    def record(**fields):
        fields["timestamp"] = datetime.now(timezone.utc).isoformat()
        fields["peak_rss_bytes"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        # macOS ru_maxrss is bytes. This experiment targets native macOS only.
        if args.device == "mps":
            fields["mps_allocated_bytes"] = torch.mps.current_allocated_memory()
            fields["mps_driver_bytes"] = torch.mps.driver_allocated_memory()
        with events.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(fields, ensure_ascii=False) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        print(json.dumps(fields, ensure_ascii=False), flush=True)

    def timeout(_signal, _frame):
        raise TimeoutError("Experiment time budget exceeded")

    signal.signal(signal.SIGALRM, timeout)
    verify_weights(args.model)
    started = time.monotonic()
    import torch
    import soundfile as sf
    from qwen_tts import Qwen3TTSModel

    torch.set_num_threads(4)
    if args.device == "mps":
        if not torch.backends.mps.is_available():
            raise RuntimeError("MPS unavailable")
        torch.mps.set_per_process_memory_fraction(0.5)
    record(event="runtime", torch=torch.__version__, device=args.device, dtype=args.dtype,
           attention=args.attention, import_seconds=time.monotonic() - started)
    signal.alarm(args.timeout)
    started = time.monotonic()
    try:
        model = Qwen3TTSModel.from_pretrained(
            str(args.model.resolve()), device_map=args.device,
            dtype=getattr(torch, args.dtype), attn_implementation=args.attention,
            local_files_only=True, use_safetensors=True,
        )
        record(event="loaded", seconds=time.monotonic() - started,
               speakers=model.get_supported_speakers())
        signal.alarm(0)
        for name, voice, text in CASES:
            if args.case and args.case != name:
                continue
            signal.alarm(args.timeout)
            started = time.monotonic()
            record(event="started", case=name, voice=voice, text=text)
            torch.manual_seed(42)
            wavs, rate = model.generate_custom_voice(
                text=text, language="Chinese", speaker=voice,
                instruct="自然清晰地讲述，像播客主持人在交谈。",
                non_streaming_mode=True, max_new_tokens=2048,
            )
            elapsed = time.monotonic() - started
            signal.alarm(0)
            wav = wavs[0]
            duration = len(wav) / rate
            if duration <= 0 or not bool(torch.isfinite(torch.as_tensor(wav)).all()):
                raise ValueError("Invalid generated audio")
            target = args.output / f"{name}.wav"
            sf.write(target, wav, rate, subtype="PCM_16")
            record(event="completed", case=name, seconds=elapsed, duration=duration,
                   rtf=elapsed / duration, sample_rate=rate,
                   sha256=hashlib.sha256(target.read_bytes()).hexdigest())
    except Exception as error:
        record(event="failed", seconds=time.monotonic() - started,
               error_type=type(error).__name__, error=str(error)[:1000])
        raise
    finally:
        signal.alarm(0)


if __name__ == "__main__":
    main()

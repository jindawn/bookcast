#!/usr/bin/env python3
"""Clean-checkout offline smoke: doctor -> mock MP3 -> chaptered M4B."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]


def run(*args: str, cwd: Path = ROOT, env: dict[str, str] | None = None) -> str:
    result = subprocess.run(args, cwd=cwd, env=env, text=True, capture_output=True,
                            check=False, timeout=180)
    if result.returncode:
        raise RuntimeError(f"command failed ({result.returncode}): {args[0]}\n{result.stdout}\n{result.stderr}")
    return result.stdout


def probe(path: Path) -> dict:
    return json.loads(run("ffprobe", "-v", "error", "-show_format", "-show_streams",
                          "-show_chapters", "-of", "json", str(path)))


def main() -> int:
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        raise RuntimeError("FFmpeg and ffprobe are required for the release smoke test")
    with tempfile.TemporaryDirectory(prefix="bookcast-ci smoke 中文 ") as raw:
        tmp = Path(raw)
        output = tmp / "output with spaces 中文"
        config = tmp / "offline-demo.toml"
        text = tmp / "验收样本.txt"
        text.write_text(
            "第一章 观察\n\n今天我们讨论如何把复杂问题拆开观察。主持人甲提出一个观点，主持人乙继续追问并给出反例。\n\n"
            "第二章 复盘\n\n把证据和猜测分开，有助于形成可检查的结论。两位主持人联系一个日常案例，并指出适用边界。\n",
            encoding="utf-8",
        )
        run("bookcast", "setup", "--profile", "demo", "--config-output", str(config))
        run("bookcast", "doctor", "--human", "--config", str(config),
            "--output-dir", str(tmp / "doctor-jobs"))
        generated = run("bookcast", "generate", str(text), "--output-dir", str(output),
                        "--config", str(config), "--mode", "two_host", "--minutes", "2")
        match = re.search(r"任务完成：([0-9a-f]{24,})", generated)
        if not match:
            raise RuntimeError(f"could not identify completed job in CLI output:\n{generated}")
        job_id = match.group(1)
        jobs = json.loads(run("bookcast", "jobs", "--json", "--output-dir", str(output)))
        if not any(job["job_id"] == job_id and job["effective_state"] == "SUCCEEDED" for job in jobs["jobs"]):
            raise RuntimeError("mock generation did not produce a successful persisted job")
        job_dir = Path(next(job["directory"] for job in jobs["jobs"] if job["job_id"] == job_id))
        mp3 = job_dir / "podcast.mp3"
        mp3_info = probe(mp3)
        if not mp3.exists() or float(mp3_info["format"]["duration"]) <= 0:
            raise RuntimeError("generated MP3 is missing or has no playable duration")
        run("bookcast", "export", job_id, "--format", "m4b", "--output-dir", str(output))
        m4b = job_dir / "podcast.m4b"
        m4b_info = probe(m4b)
        audio_streams = [stream for stream in m4b_info["streams"] if stream.get("codec_type") == "audio"]
        if not audio_streams or not m4b_info.get("chapters"):
            raise RuntimeError("M4B must have an audio stream and chapter markers")
        print(json.dumps({"doctor": "passed", "mock_job": job_id, "mp3_bytes": mp3.stat().st_size,
                          "m4b_bytes": m4b.stat().st_size, "m4b_chapters": len(m4b_info["chapters"]),
                          "network_ai_calls": 0}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, RuntimeError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
        print(f"release smoke failed: {exc}", file=sys.stderr)
        sys.exit(1)

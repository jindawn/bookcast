"""Opt-in full-book release exercise; never runs in default pytest or pull-request CI."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


pytestmark = [
    pytest.mark.large_model,
    pytest.mark.skipif(os.getenv("BOOKCAST_RUN_RELEASE_LARGE_BOOK") != "1",
                       reason="set BOOKCAST_RUN_RELEASE_LARGE_BOOK=1 for the network/full-book Mock acceptance"),
]


def cli(*args: str, cwd: Path, timeout: int = 900) -> str:
    result = subprocess.run(["bookcast", *args], cwd=cwd, text=True, capture_output=True,
                            timeout=timeout, check=False)
    assert result.returncode == 0, f"bookcast {' '.join(args)} failed:\n{result.stdout}\n{result.stderr}"
    return result.stdout


def test_public_domain_full_book_mock_resume_and_m4b(tmp_path: Path) -> None:
    assert shutil.which("ffmpeg") and shutil.which("ffprobe")
    imported = tmp_path / "clean imports"
    output = tmp_path / "clean output"
    acquired = json.loads(cli("acquire", "The Wealth of Nations", "--edition", "gutenberg:3300",
                              "--output-dir", str(imported), "--max-size-mb", "32",
                              "--download-timeout", "300", cwd=tmp_path))
    assert acquired["status"] == "parsed"
    source = Path(acquired["file"])
    metadata = json.loads((Path(acquired["directory"]) / "metadata.json").read_text(encoding="utf-8"))
    text = source.read_text(encoding="utf-8", errors="strict")
    assert metadata["acquisition"]["source"]["rights_category"] == "public_domain"
    assert metadata["acquisition"]["source"]["jurisdiction"] == "US"
    assert len(metadata["chapter_ids"]) >= 60
    assert len(text) >= 1_000_000
    acquired_bytes = source.stat().st_size

    generated = cli("generate", str(source), "--output-dir", str(output), "--mode", "summary",
                    "--minutes", "10", cwd=tmp_path, timeout=1800)
    assert "任务完成：" in generated
    inventory = json.loads(cli("jobs", "--json", "--output-dir", str(output), cwd=tmp_path))
    assert len(inventory["jobs"]) == 1
    job = inventory["jobs"][0]
    assert job["effective_state"] == "SUCCEEDED"
    job_id, job_path = job["job_id"], Path(job["directory"])
    manifest_before = (job_path / "manifest.json").read_bytes()
    disk_before_resume = sum(p.stat().st_size for p in job_path.rglob("*") if p.is_file())
    cli("generate", str(source), "--output-dir", str(output), "--resume", "--mode", "summary",
        "--minutes", "10", cwd=tmp_path, timeout=1800)
    assert (job_path / "manifest.json").read_bytes() == manifest_before
    assert sum(p.stat().st_size for p in job_path.rglob("*") if p.is_file()) == disk_before_resume

    cli("export", book_id, "--format", "m4b", "--output-dir", str(output), cwd=tmp_path)
    info = json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_format", "-show_streams",
                                     "-show_chapters", "-of", "json", str(job_path / "podcast.m4b")],
                                    capture_output=True, text=True, check=True).stdout)
    assert info["format"]["format_name"].find("mov") >= 0
    assert any(stream.get("codec_type") == "audio" and stream.get("codec_name") == "aac"
               for stream in info["streams"])
    assert len(info["chapters"]) > 1
    chapter_ends = [float(chapter["end_time"]) for chapter in info["chapters"]]
    assert chapter_ends == sorted(chapter_ends)
    assert chapter_ends[-1] <= float(info["format"]["duration"]) + 0.1
    disk_after = sum(p.stat().st_size for p in job_path.rglob("*") if p.is_file())
    total_disk = sum(p.stat().st_size for base in (imported, output) for p in base.rglob("*") if p.is_file())
    assert disk_after > disk_before_resume and total_disk > acquired_bytes

"""Opt-in acceptance of an already completed real local job; never synthesizes."""
import importlib.util
import os
from pathlib import Path

import pytest

from bookcast.audio import validate_wav
from bookcast.pipeline import load_manifest
from bookcast.storage import sha256_file

pytestmark = pytest.mark.skipif(os.environ.get("BOOKCAST_VERIFY_LOCAL_QWEN") != "1",
                               reason="requires explicit local Qwen artifacts/runtime; no model download in CI")


def test_real_qwen_job_resumes_without_loading_or_inference(monkeypatch):
    root = Path(os.environ["BOOKCAST_QWEN_OUTPUT"]).resolve()
    manifest = load_manifest(root / "manifest.json")
    assert manifest.status == "completed"
    calls = [a for a in manifest.ai_calls if a.kind == "tts" and a.status == "completed"]
    assert calls and all(a.model == "Qwen3-TTS-12Hz-1.7B-CustomVoice" for a in calls)
    for wav in (root / "audio/units").glob("*.wav"):
        validate_wav(wav)
    before = {p.relative_to(root): (sha256_file(p), p.stat().st_mtime_ns)
              for p in root.rglob("*") if p.is_file()}
    def forbidden(*args, **kwargs):
        pytest.fail("completed local resume must not load a model, infer or request HTTP")
    monkeypatch.setattr("bookcast.adapters.qwen.QwenTTSProvider._load", forbidden)
    monkeypatch.setattr("bookcast.adapters.qwen.QwenTTSProvider.synthesize_unit", forbidden)
    monkeypatch.setattr("urllib.request.OpenerDirector.open", forbidden)
    script = Path(__file__).resolve().parents[1] / "scripts/tts_ab.py"
    spec = importlib.util.spec_from_file_location("qwen_live_ab", script)
    ab = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ab)
    ab.render(root)  # CachedLLM in this client independently forbids all LLM inference.
    assert before == {p.relative_to(root): (sha256_file(p), p.stat().st_mtime_ns)
                      for p in root.rglob("*") if p.is_file()}

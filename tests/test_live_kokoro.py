"""Zero-synthesis resume check for an already completed local Kokoro job."""
import importlib.util
import os
from pathlib import Path

import pytest

from bookcast.audio import validate_wav
from bookcast.pipeline import load_manifest
from bookcast.storage import sha256_file

pytestmark = pytest.mark.skipif(
    os.environ.get("BOOKCAST_VERIFY_KOKORO") != "1",
    reason="requires an existing completed Kokoro job; this test never synthesizes",
)


def test_real_kokoro_job_resumes_without_synthesis_or_llm(monkeypatch):
    root = Path(os.environ["BOOKCAST_KOKORO_OUTPUT"]).resolve()
    manifest = load_manifest(root / "manifest.json")
    assert manifest.status == "completed"
    for wav in (root / "audio/units").glob("*.wav"):
        validate_wav(wav)
    before = {p.relative_to(root): (sha256_file(p), p.stat().st_mtime_ns)
              for p in root.rglob("*") if p.is_file()}

    def forbidden(*args, **kwargs):
        pytest.fail("completed Kokoro resume must not synthesize")

    monkeypatch.setattr("bookcast.adapters.kokoro.KokoroTTSProvider.synthesize_unit", forbidden)
    monkeypatch.setattr("urllib.request.OpenerDirector.open", forbidden)
    script = Path(__file__).resolve().parents[1] / "scripts/tts_ab.py"
    spec = importlib.util.spec_from_file_location("kokoro_live_ab", script)
    ab = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ab)
    ab.render(root)  # CachedLLM used by this client forbids any new LLM request.

    assert before == {p.relative_to(root): (sha256_file(p), p.stat().st_mtime_ns)
                      for p in root.rglob("*") if p.is_file()}

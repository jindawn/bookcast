"""Explicit, bounded live acceptance; never called by the default test suite.

Prepare a Phase 9 audio-only fork with scripts/tts_ab.py first. A failed or new
fork may consume API quota when explicitly enabled. A complete fork must not.
"""
import importlib.util
import os
from pathlib import Path

import pytest

from bookcast.audio import validate_wav
from bookcast.pipeline import load_manifest
from bookcast.storage import sha256_file

pytestmark = pytest.mark.skipif(os.environ.get('BOOKCAST_RUN_LIVE_GEMINI') != '1',
                                reason='official cloud TTS acceptance requires explicit opt-in')


def test_real_gemini_fork_and_no_request_resume(monkeypatch):
    root = Path(os.environ['BOOKCAST_LIVE_GEMINI_OUTPUT'])
    manifest = load_manifest(root / 'manifest.json')
    assert manifest.status == 'completed'
    before = {p.relative_to(root): (sha256_file(p), p.stat().st_mtime_ns)
              for p in root.rglob('*') if p.is_file()}

    def forbidden(*args, **kwargs):
        pytest.fail('completed Gemini resume must not send an API request')
    monkeypatch.setattr('bookcast.adapters.gemini.GeminiTTSProvider._request', forbidden)
    script = Path(__file__).resolve().parents[1] / 'scripts/tts_ab.py'
    spec = importlib.util.spec_from_file_location('live_ab', script)
    ab = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ab)
    ab.render(root)
    assert before == {p.relative_to(root): (sha256_file(p), p.stat().st_mtime_ns)
                      for p in root.rglob('*') if p.is_file()}
    manifest = load_manifest(root / 'manifest.json')
    calls = [a for a in manifest.ai_calls if a.kind == 'tts' and a.status == 'completed']
    assert calls and all(a.model.startswith('gemini-') for a in calls)
    assert all(a.task.startswith('tts_segment:') for a in calls)
    for wav in (root / 'audio/segments').glob('*.wav'):
        validate_wav(wav)

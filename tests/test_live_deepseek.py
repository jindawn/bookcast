"""Explicit opt-in, bounded self-authored content; no paid API in default pytest.

This checks real LLM content only. Full Kokoro acceptance uses the documented CLI.
"""
import os
import json
from pathlib import Path

import pytest

from bookcast.composition import configured_pipeline
from bookcast.pipeline import load_manifest
from bookcast.provider_config import load_config, ProviderSpec
from bookcast.storage import sha256_file

pytestmark = [pytest.mark.live_llm, pytest.mark.skipif(
    os.environ.get('BOOKCAST_RUN_LIVE_LLM') != '1' or not os.environ.get('DEEPSEEK_API_KEY'),
    reason='requires BOOKCAST_RUN_LIVE_LLM=1 and DEEPSEEK_API_KEY (paid API)')]


def test_real_structured_content_and_no_network_on_completed_resume(tmp_path, monkeypatch):
    root = Path(__file__).parents[1]
    settings = load_config(root/'examples/deepseek-kokoro.toml')
    # Speech is outside this bounded LLM test; it must never be reported as real audio.
    settings.providers = [settings.providers[0], ProviderSpec(name='test-tone', kind='tts', type='mock', model='mock-tones-v1')]
    settings.tts_priority = ['test-tone']
    pipe = configured_pipeline(settings, tmp_path/'output')
    job = pipe.generate(root/'examples/content-demo.txt', mode='two_host', minutes=2)
    manifest = load_manifest(job/'manifest.json')
    assert manifest.status == 'completed'
    calls = [c for c in manifest.ai_calls if c.kind=='llm']
    assert calls and all(c.provider == 'deepseek' and c.status == 'completed' for c in calls)
    assert any(c.task.startswith('analysis:') for c in calls)
    assert any(c.task.startswith('synthesis/book/') for c in calls)
    assert any(c.task.startswith('script:') for c in calls)
    assert all(not json.loads(p.read_text())['is_mock'] for p in (job/'scripts').glob('*.json'))
    assert all(not json.loads(p.read_text())['is_mock'] for p in (job/'analysis/chunks').glob('*.json'))
    before = {p:(p.stat().st_mtime_ns,sha256_file(p)) for p in job.rglob('*') if p.is_file() and p.name!='.lock'}
    def forbidden(*args, **kwargs):
        pytest.fail('completed resume attempted another network request')
    monkeypatch.setattr('bookcast.adapters.compatible.request.build_opener', forbidden)
    configured_pipeline(settings, tmp_path/'output').resume_job(job)
    assert all((p.stat().st_mtime_ns,sha256_file(p))==v for p,v in before.items())

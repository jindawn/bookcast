from pathlib import Path
import importlib.util

import pytest

from bookcast.composition import configured_pipeline
from bookcast.errors import BookCastError
from bookcast.pipeline import load_manifest
from bookcast.provider_config import load_config
from bookcast.storage import sha256_file

path = Path(__file__).resolve().parents[1] / 'scripts/tts_ab.py'
spec = importlib.util.spec_from_file_location('tts_ab', path)
ab = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ab)


def test_audio_fork_preserves_content_and_source_without_llm(tmp_path, monkeypatch):
    config = load_config()
    source = configured_pipeline(config, tmp_path / 'source').generate(path.parents[1] / 'examples/content-demo.txt', minutes=2)
    before = {p.relative_to(source): sha256_file(p) for p in source.rglob('*') if p.is_file()}
    target = ab.prepare(source, tmp_path / 'fork', config)
    def forbidden(*a, **k):
        raise AssertionError('LLM must never execute')
    monkeypatch.setattr('bookcast.providers.MockLLMProvider.generate_structured', forbidden)
    ab.render(target)
    first = load_manifest(target / 'manifest.json')
    assert first.status == 'completed'
    assert first.job_id != load_manifest(source / 'manifest.json').job_id
    ab.render(target)
    assert load_manifest(target / 'manifest.json').ai_calls == first.ai_calls
    assert before == {p.relative_to(source): sha256_file(p) for p in source.rglob('*') if p.is_file()}
    assert {p.name: sha256_file(p) for p in (target / 'scripts').glob('*.json')} == {
        p.name: sha256_file(p) for p in (source / 'scripts').glob('*.json')}
    with pytest.raises(BookCastError):
        ab.prepare(source, target, config)
    # Invalidated content fails before any upstream request.
    artifact = next(iter(next(a for a in first.ai_calls if a.kind == 'llm').artifacts))
    (target / artifact).write_text('{}')
    with pytest.raises(BookCastError, match='business_error'):
        ab.render(target)

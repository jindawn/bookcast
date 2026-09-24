"""Offline adapter/registry/cache tests; no real torch, Qwen, or weights required."""
from pathlib import Path
from types import SimpleNamespace
import sys

import pytest
from pydantic import ValidationError

from bookcast.adapters import qwen, qwen_assets
from bookcast.audio import validate_wav
from bookcast.errors import BookCastError
from bookcast.pipeline import Pipeline, load_manifest
from bookcast.provider_api import ErrorKind, ProviderError, SpeechUnit
from bookcast.provider_config import ProviderSpec, QwenTTSConfig, load_config
from bookcast.provider_registry import default_registry
from bookcast.providers import MockLLMProvider
from bookcast.storage import sha256_file

DEMO = Path(__file__).resolve().parents[1] / "examples/content-demo.txt"


def spec(root, **settings):
    return ProviderSpec(name="qwen", kind="tts", type="qwen-local", model=qwen_assets.MODEL,
                        local_tts=QwenTTSConfig(model_dir=str(root), experimental=True, **settings))


@pytest.mark.parametrize("settings", [dict(experimental=False), dict(experimental="true"),
                                     dict(host_voice="not-a-voice"), dict(host_voice="Uncle_Fu"),
                                     dict(style_instruction="x" * 257), dict(secret="not-allowed")])
def test_strict_experimental_config(settings):
    with pytest.raises(ValidationError):
        QwenTTSConfig(**{**dict(model_dir="model", experimental=True), **settings})


def test_registry_is_lazy_and_rejects_remote_settings(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "torch", None)
    monkeypatch.setitem(sys.modules, "qwen_tts", None)
    provider = default_registry().create(spec(tmp_path))
    assert provider.capabilities().speech_units and provider.capabilities().local
    assert provider.health_check().last_error == ErrorKind.INPUT
    for field in ("base_url", "api_key_env"):
        with pytest.raises(ValidationError):
            ProviderSpec.model_validate({**spec(tmp_path).model_dump(), field: "secret"})
    config = load_config(DEMO.parents[1] / "examples/qwen-local.toml")
    assert Path(config.providers[-1].local_tts.model_dir).is_absolute()


def test_assets_reject_corruption_and_symlinks(tmp_path, monkeypatch):
    asset = tmp_path / "config.json"
    asset.write_bytes(b"official fixture")
    monkeypatch.setattr(qwen_assets, "FILES", {"config.json": sha256_file(asset)})
    assert qwen_assets.verify_assets(tmp_path)
    asset.write_bytes(b"changed config")
    with pytest.raises(BookCastError, match="校验失败"):
        qwen_assets.verify_assets(tmp_path)
    target = tmp_path / "outside"
    asset.rename(target)
    asset.symlink_to(target)
    with pytest.raises(BookCastError):
        qwen_assets.verify_assets(tmp_path)


def test_assets_reject_unpinned_optional_processor_and_symlinks(tmp_path, monkeypatch):
    asset = tmp_path / "preprocessor_config.json"
    asset.write_text('{"processor": "official"}')
    monkeypatch.setattr(qwen_assets, "FILES", {asset.name: sha256_file(asset)})
    assert qwen_assets.verify_assets(tmp_path)

    # Transformers looks for this optional name ahead of the pinned one.
    optional = tmp_path / "processor_config.json"
    optional.write_text('{"processor": "attacker"}')
    with pytest.raises(BookCastError, match="校验失败"):
        qwen_assets.verify_assets(tmp_path)
    optional.unlink()

    outside = tmp_path.parent / f"{tmp_path.name}-outside-processor.json"
    outside.write_text('{}')
    optional.symlink_to(outside)
    with pytest.raises(BookCastError, match="校验失败"):
        qwen_assets.verify_assets(tmp_path)


@pytest.fixture
def runtime(monkeypatch):
    np = pytest.importorskip("numpy")
    monkeypatch.setattr(qwen, "verify_assets", lambda root: {"weights": "verified"})
    monkeypatch.setattr(qwen, "version", lambda name: "fixed")
    monkeypatch.setitem(sys.modules, "torch", SimpleNamespace(
        manual_seed=lambda seed: None, mps=SimpleNamespace(empty_cache=lambda: None)))
    return np


def test_cache_covers_voice_style_seed_and_runtime_not_install_path(tmp_path, runtime, monkeypatch):
    base = qwen.QwenTTSProvider(spec(tmp_path)).cache_key
    assert qwen.QwenTTSProvider(spec(tmp_path / "moved")).cache_key == base
    for settings in [dict(host_voice="Uncle_Fu", guest_voice="Vivian"), dict(seed=43),
                     dict(style_instruction="沉稳地讲述")]:
        assert qwen.QwenTTSProvider(spec(tmp_path, **settings)).cache_key != base
    monkeypatch.setattr(qwen, "version", lambda name: "updated")
    assert qwen.QwenTTSProvider(spec(tmp_path)).cache_key != base


def fake_provider(root, np, **settings):
    provider = qwen.QwenTTSProvider(spec(root, **settings))
    calls = []
    def generate(**kwargs):
        calls.append(kwargs)
        return [np.array([.1, -.2] * 2400)], 24000
    provider._engine = SimpleNamespace(generate_custom_voice=generate)
    return provider, calls


def test_standard_audio_and_safe_permanent_errors(tmp_path, runtime):
    provider, calls = fake_provider(tmp_path, runtime)
    for speaker, voice in [("主持人", "Vivian"), ("嘉宾", "Uncle_Fu")]:
        info = provider.synthesize_unit(SpeechUnit(speaker=speaker, text="中文测试。"), tmp_path / "test.wav")
        assert info.voice == voice and info.audio_kind == "speech"
        validate_wav(tmp_path / "test.wav")
    assert [c["speaker"] for c in calls] == ["Vivian", "Uncle_Fu"]
    for samples, rate in [([], 24000), ([float("nan")], 24000), ([0.], 24000), ([.1], 16000)]:
        provider._engine = SimpleNamespace(generate_custom_voice=lambda **kw: ([samples], rate))
        with pytest.raises(ProviderError) as error:
            provider.synthesize_unit(SpeechUnit(speaker="主持人", text="测试。"), tmp_path / "bad.wav")
        assert error.value.kind == ErrorKind.SCHEMA
    def broken(**kwargs):
        raise RuntimeError("upstream secret must not be persisted")
    provider._engine = SimpleNamespace(generate_custom_voice=broken)
    with pytest.raises(ProviderError) as error:
        provider.synthesize_unit(SpeechUnit(speaker="主持人", text="测试。"), tmp_path / "bad.wav")
    assert error.value.kind == ErrorKind.INPUT
    assert "secret" not in str(error.value)
    assert not provider.last_status.retryable


def test_core_resume_and_voice_change_do_not_repeat_llm(tmp_path, runtime):
    provider, calls = fake_provider(tmp_path, runtime)
    pipeline = Pipeline(MockLLMProvider(), provider, tmp_path / "out")
    root = pipeline.generate(DEMO, minutes=1)
    before = load_manifest(root / "manifest.json")
    llm = [c.model_dump() for c in before.ai_calls if c.kind == "llm"]
    count = len(calls)
    hashes = {p: (sha256_file(p), p.stat().st_mtime_ns) for p in root.rglob("*") if p.is_file() and p.name != ".lock"}
    pipeline.resume_job(root)
    assert len(calls) == count
    assert hashes == {p: (sha256_file(p), p.stat().st_mtime_ns) for p in hashes}
    changed, regenerated = fake_provider(tmp_path, runtime, host_voice="Uncle_Fu", guest_voice="Vivian")
    Pipeline(MockLLMProvider(), changed, root.parent).resume_job(root)
    assert len(regenerated) == count
    after = load_manifest(root / "manifest.json")
    assert [c.model_dump() for c in after.ai_calls if c.kind == "llm"] == llm

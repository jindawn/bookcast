"""Offline tests of native-adapter boundaries, installer, and real Core recovery."""
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import time
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from bookcast.adapters.kokoro import KokoroTTSProvider
from bookcast.audio import validate_wav, wav_seconds
from bookcast.cli import app
from bookcast.errors import BookCastError
from bookcast.pipeline import Pipeline, load_manifest
from bookcast.provider_api import ErrorKind, ProviderCapabilities, ProviderError, SpeechInfo, SpeechUnit
from bookcast.provider_chain import ProviderChain
from bookcast.provider_config import ProviderSpec, LocalTTSConfig, load_config
from bookcast.providers import MockLLMProvider, MockTTSProvider
from bookcast.speech import split_text
from bookcast.storage import cleanup_orphan_temporary_artifacts, fingerprint, sha256_file
from bookcast import tts_setup

DEMO = Path(__file__).resolve().parents[1] / "examples/content-demo.txt"


def spec(tmp_path, **settings):
    return ProviderSpec(name="kokoro", kind="tts", type="kokoro-local", model=tts_setup.MODEL,
                        local_tts=LocalTTSConfig(model_dir=str(tmp_path), **settings))


class UnitFake(MockTTSProvider):
    """Synthetic WAV at the adapter boundary; does not claim to test model quality."""
    def __init__(self, name="local", fail_at=None, error=ErrorKind.QUOTA, key="v1"):
        self.name, self.fail_at, self.error, self.cache_key = name, fail_at, error, key
        self.calls = []

    def capabilities(self):
        return ProviderCapabilities(speech=True, speech_units=True, local=True)

    def synthesize_unit(self, unit, destination):
        self.calls.append(unit)
        if len(self.calls) == self.fail_at:
            raise ProviderError(self.error)
        super().synthesize_unit(unit, destination)
        return SpeechInfo(audio_kind="speech", voice="fixture")


def test_split_preserves_text_and_bounds():
    text = "欢迎收听。数字 3.14 和 English。" + "中文" * 400 + "！\n尾声。"
    chunks = split_text(text)
    assert "".join(chunks) == text
    assert all(0 < len(c) <= 80 for c in chunks)
    assert "3.14" in chunks[0]
    assert split_text("中" * 80 + "。”") == ["中" * 79, "中。”"]


def test_cache_key_covers_assets_runtime_and_voice_not_install_path(tmp_path, monkeypatch):
    monkeypatch.setattr("bookcast.adapters.kokoro.verify_model", lambda p: "assets-v1")
    monkeypatch.setattr("bookcast.adapters.kokoro.version", lambda name: "1")
    baseline = KokoroTTSProvider(spec(tmp_path)).cache_key
    assert KokoroTTSProvider(spec(tmp_path / "moved")).cache_key == baseline
    assert KokoroTTSProvider(spec(tmp_path, host_voice=46)).cache_key != baseline
    assert KokoroTTSProvider(spec(tmp_path, speed=1.1)).cache_key != baseline
    monkeypatch.setattr("bookcast.adapters.kokoro.verify_model", lambda p: "assets-v2")
    assert KokoroTTSProvider(spec(tmp_path)).cache_key != baseline


def test_downloader_limit_and_redirect_allowlist(tmp_path, monkeypatch):
    fake = SimpleNamespace(open=lambda *a, **k: io.BytesIO(b"too much data"))
    monkeypatch.setattr(tts_setup.request, "build_opener", lambda *a: fake)
    monkeypatch.setattr(tts_setup, "MODEL_BYTES", 2)
    with pytest.raises(BookCastError, match="超过"):
        tts_setup.download_model(tmp_path / "download")
    with pytest.raises(BookCastError, match="重定向"):
        tts_setup.ModelRedirect().redirect_request(None, None, 302, "", {}, "https://example.org/file")


def test_no_native_sdk_import_in_pipeline():
    root = DEMO.parents[1] / "src/bookcast"
    for name in ("pipeline.py", "content.py", "speech.py", "web_api.py", "web_service.py"):
        assert "import sherpa" not in (root / name).read_text()


@pytest.mark.parametrize("field,value", [("host_voice", 44), ("guest_voice", 53), ("speed", 0),
                                         ("speed", float("nan")), ("threads", 0)])
def test_local_config_rejects_invalid_settings(tmp_path, field, value):
    with pytest.raises(ValidationError):
        spec(tmp_path, **{field: value})


def test_config_paths_and_no_overwrite(tmp_path, monkeypatch):
    target = tmp_path / "native.toml"
    tts_setup.write_local_config(target, tmp_path / "models")
    text = target.read_text().replace(str(tmp_path / "models"), "models")
    target.write_text(text)
    monkeypatch.chdir(tmp_path.parent)
    config = load_config(target)
    assert config.providers[1].local_tts.model_dir == str(tmp_path / "models")
    with pytest.raises(BookCastError, match="未覆盖"):
        tts_setup.write_local_config(target, tmp_path)
    assert target.read_text() == text
    assert config.tts_priority == ["kokoro"]
    assert all(p.api_key_env is None for p in config.providers)


def test_missing_runtime_is_optional_and_doctor_actionable(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "sherpa_onnx", None)
    provider = KokoroTTSProvider(spec(tmp_path))
    assert provider.health_check().last_error == ErrorKind.INPUT
    target = tmp_path / "config.toml"
    tts_setup.write_local_config(target, tmp_path)
    result = CliRunner().invoke(app, ["doctor", "--config", str(target), "--output-dir", str(tmp_path / "out")])
    assert result.exit_code == 1
    assert "uv sync --extra tts" in result.output
    result = CliRunner().invoke(app, ["config", "providers", "--config", str(target)])
    assert result.exit_code == 0  # listing does not import a native runtime


def test_adapter_lazy_cpu_voices_and_invalid_audio(tmp_path, monkeypatch):
    np = pytest.importorskip("numpy")
    provider = KokoroTTSProvider(spec(tmp_path, speed=1.2))
    calls = []
    def generate(text, **kwargs):
        calls.append((text, kwargs))
        return SimpleNamespace(samples=np.array([.1, -.2] * 100), sample_rate=24000)
    provider._engine = SimpleNamespace(generate=generate)
    for speaker, voice in [("主持人", "zf_xiaobei"), ("嘉宾", "zm_yunxi")]:
        result = provider.synthesize_unit(SpeechUnit(speaker=speaker, text="中文测试。"), tmp_path / "test.wav")
        assert result.voice == voice and result.audio_kind == "speech"
        validate_wav(tmp_path / "test.wav")
    assert [kwargs["sid"] for _, kwargs in calls] == [45, 50]
    assert all(kwargs["speed"] == 1.2 for _, kwargs in calls)
    for samples in [[], [float("nan")], [0., 0.]]:
        provider._engine = SimpleNamespace(generate=lambda *a, **k: SimpleNamespace(samples=samples, sample_rate=24000))
        with pytest.raises(ProviderError) as failure:
            provider.synthesize_unit(SpeechUnit(speaker="主持人", text="测试"), tmp_path / "bad.wav")
        assert failure.value.kind == ErrorKind.SCHEMA


def make_archive(tmp_path, entries=None):
    path = tmp_path / "model.tar.bz2"
    with tarfile.open(path, "w:bz2") as tar:
        for name, payload, kind in entries or [(f"{tts_setup.MODEL}/{p}", b"model", tarfile.REGTYPE)
                for p in [*tts_setup.REQUIRED, "espeak-ng-data/dict"]]:
            item = tarfile.TarInfo(name)
            item.type, item.size = kind, len(payload)
            tar.addfile(item, io.BytesIO(payload))
    return path


def test_installer_atomic_integrity_and_reuse(tmp_path, monkeypatch):
    archive = make_archive(tmp_path)
    monkeypatch.setattr(tts_setup, "MODEL_SHA256", sha256_file(archive))
    monkeypatch.setattr(tts_setup, "MODEL_BYTES", archive.stat().st_size)
    root = tts_setup.install_model(tmp_path / "models", archive)
    receipt_path = root / tts_setup.RECEIPT
    official_files = json.loads(receipt_path.read_text())["files"]
    monkeypatch.setattr(tts_setup, "OFFICIAL_ASSET_FINGERPRINT", fingerprint(official_files))
    before = (root / "model.onnx").stat().st_mtime_ns
    assert tts_setup.install_model(root.parent) == root  # no download on repeat
    assert (root / "model.onnx").stat().st_mtime_ns == before
    (root / "model.onnx").write_text("corrupt")
    with pytest.raises(BookCastError, match="校验失败"):
        tts_setup.install_model(root.parent)
    # Rewriting the mutable receipt used to make tampered weights appear official.
    forged_receipt = json.loads(receipt_path.read_text())
    forged_receipt["files"]["model.onnx"] = sha256_file(root / "model.onnx")
    receipt_path.write_text(json.dumps(forged_receipt))
    with pytest.raises(BookCastError, match="校验失败"):
        tts_setup.verify_model(root)
    assert (root / "model.onnx").read_text() == "corrupt"  # never overwrite user data


@pytest.mark.parametrize("name,kind", [("../escape", tarfile.REGTYPE), ("/tmp/escape", tarfile.REGTYPE),
    (f"{tts_setup.MODEL}/../escape", tarfile.REGTYPE), (f"{tts_setup.MODEL}/link", tarfile.SYMTYPE),
    (f"{tts_setup.MODEL}/link", tarfile.LNKTYPE)])
def test_archive_rejects_unsafe_entries(tmp_path, name, kind):
    archive = make_archive(tmp_path, [(name, b"", kind)])
    with pytest.raises(BookCastError, match="不安全"):
        tts_setup.unpack_model(archive, tmp_path / "stage")


def test_wrong_checksum_and_unpack_limit(tmp_path, monkeypatch):
    archive = make_archive(tmp_path)
    with pytest.raises(BookCastError, match="SHA-256"):
        tts_setup.install_model(tmp_path / "models", archive)
    assert not (tmp_path / "models" / tts_setup.MODEL).exists()
    monkeypatch.setattr(tts_setup, "MAX_UNPACKED", 1)
    with pytest.raises(BookCastError, match="超过限制"):
        tts_setup.unpack_model(archive, tmp_path / "stage")


@pytest.mark.parametrize("failure", [ErrorKind.QUOTA, ErrorKind.TIMEOUT])
def test_unit_failover_resume_and_config_invalidation(tmp_path, failure):
    primary = UnitFake("A", fail_at=3, error=failure)
    secondary = UnitFake("B")
    pipeline = Pipeline(MockLLMProvider(), ProviderChain([primary, secondary]), tmp_path)
    root = pipeline.generate(DEMO, minutes=1)
    manifest = load_manifest(root / "manifest.json")
    units = [c for c in manifest.ai_calls if c.kind == "tts" and c.status == "completed"]
    assert len(primary.calls) == 3 and len(secondary.calls) == len(units) - 2
    assert secondary.calls[0] == primary.calls[-1]
    assert json.loads((root / "audio/export.json").read_text())["audio_kind"] == "speech"
    before = {p: (sha256_file(p), p.stat().st_mtime_ns) for p in root.rglob("*") if p.is_file() and p.name != ".lock"}
    pipeline.resume_job(root)
    assert all((sha256_file(p), p.stat().st_mtime_ns) == value for p, value in before.items())
    assert len(primary.calls) == 3 and len(secondary.calls) == len(units) - 2
    llm_count = len([c for c in manifest.ai_calls if c.kind == "llm"])
    changed = UnitFake("B", key="new-voice")
    Pipeline(MockLLMProvider(), ProviderChain([UnitFake("A"), changed]), tmp_path).resume_job(root)
    assert len(changed.calls) == len(units) - 2
    after = load_manifest(root / "manifest.json")
    assert len([c for c in after.ai_calls if c.kind == "llm"]) == llm_count


def test_permanent_unit_failure_does_not_failover(tmp_path):
    secondary = UnitFake("B")
    with pytest.raises(BookCastError, match="schema_error"):
        Pipeline(MockLLMProvider(), ProviderChain([UnitFake("A", fail_at=2, error=ErrorKind.SCHEMA), secondary]), tmp_path).generate(DEMO, minutes=1)
    assert not secondary.calls
    assert load_manifest(next(tmp_path.glob('*/manifest.json'))).error_kind == "schema_error"


def test_repair_only_damaged_unit_and_replace_existing_mock_for_real_voice(tmp_path):
    mock_root = Pipeline(MockLLMProvider(), MockTTSProvider(), tmp_path / "old").generate(DEMO, minutes=1)
    analysis = {p: sha256_file(p) for p in (mock_root / 'analysis').rglob('*.json')}
    native = UnitFake("new-provider")
    Pipeline(MockLLMProvider(), native, tmp_path / "old").resume_job(mock_root)
    assert native.calls
    assert json.loads((mock_root / "audio/export.json").read_text())["audio_kind"] == "speech"
    assert all(sha256_file(p) == digest for p, digest in analysis.items())
    assert all(call.provider == 'new-provider' for call in load_manifest(mock_root / 'manifest.json').ai_calls
               if call.kind == 'tts' and call.status == 'completed' and call.task.startswith('tts:')
               and call.artifacts == load_manifest(mock_root / 'manifest.json').steps[call.task].artifacts)
    before = len(native.calls)
    Pipeline(MockLLMProvider(), native, tmp_path / "old").resume_job(mock_root)
    assert len(native.calls) == before
    root = Pipeline(MockLLMProvider(), native, tmp_path / "new").generate(DEMO, minutes=1)
    before = len(native.calls)
    audio = sorted((root / "audio/units").glob("*.wav"))
    keep = audio[0].stat().st_mtime_ns, sha256_file(audio[0])
    audio[-1].write_bytes(b"damaged")
    Pipeline(MockLLMProvider(), native, tmp_path / "new").resume_job(root)
    assert len(native.calls) == before + 1
    assert (audio[0].stat().st_mtime_ns, sha256_file(audio[0])) == keep
    validate_wav(audio[-1])


def test_real_voice_unavailable_never_completes_with_old_mock_audio(tmp_path):
    root = Pipeline(MockLLMProvider(), MockTTSProvider(), tmp_path).generate(DEMO, minutes=1)
    failing = UnitFake('kokoro', fail_at=1, error=ErrorKind.AUTH)
    with pytest.raises(BookCastError, match='authentication_error'):
        Pipeline(MockLLMProvider(), failing, tmp_path).resume_job(root)
    manifest = load_manifest(root / 'manifest.json')
    assert manifest.status == 'failed'
    assert any(call.provider == 'kokoro' and call.error == 'authentication_error'
               for call in manifest.ai_calls if call.kind == 'tts')
    assert json.loads((root / 'audio/export.json').read_text())['audio_kind'] == 'mock'


def test_real_tts_chain_rejects_mock_fallback(tmp_path):
    with pytest.raises(BookCastError, match='禁止回退 Mock'):
        Pipeline(MockLLMProvider(), ProviderChain([UnitFake('kokoro'), MockTTSProvider()]), tmp_path)


KILL_SCRIPT = r'''
import sys, time
from pathlib import Path
from bookcast.pipeline import Pipeline
import bookcast.pipeline as core
import bookcast.speech as speech
from bookcast.storage import atomic_target
from bookcast.providers import MockLLMProvider
sys.path.insert(0, str(Path.cwd() / 'tests'))
from test_tts import UnitFake
source, output, ready, window = sys.argv[1:]
observe = core._Runner.observe
def observed(self, attempt):
    observe(self, attempt)
    if window == 'completed' and attempt.task == 'tts:0001:0002-0001' and attempt.status == 'completed':
        orphan = self.root / 'audio/units/.0001-0002-0001.wav.abcdefgh.tmp'
        orphan.write_bytes(b'')
        Path(ready).write_text('ready')
        while True: time.sleep(.05)
core._Runner.observe = observed
if window == 'running':
    original = speech.atomic_target
    from contextlib import contextmanager
    @contextmanager
    def interrupted_target(path):
        with original(path) as temporary:
            if path.name == '0001-0002-0001.wav':
                temporary.write_bytes(b'partial audio')
                Path(ready).write_text('ready')
                while True: time.sleep(.05)
            yield temporary
    speech.atomic_target = interrupted_target
Pipeline(MockLLMProvider(), UnitFake(), Path(output)).generate(Path(source), minutes=1)
'''


@pytest.mark.parametrize("window", ["running", "completed"])
def test_sigkill_recovers_minimum_unit(tmp_path, window):
    output, ready = tmp_path / "out", tmp_path / "ready"
    process = subprocess.Popen([sys.executable, "-c", KILL_SCRIPT, str(DEMO), str(output), str(ready), window],
                               stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        deadline = time.monotonic() + 30
        while not ready.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(.02)
        assert ready.exists()
        root = next(output.iterdir())
        first = root / "audio/units/0001-0001-0001.wav"
        before = sha256_file(first), first.stat().st_mtime_ns
        process.kill()
        process.wait(timeout=10)
        if window == "running":
            orphan = next((root / "audio/units").glob(".bookcast-tmp-0001-0002-0001.wav.*.tmp"))
            assert orphan.read_bytes() == b"partial audio"
        else:
            orphan = root / "audio/units/.0001-0002-0001.wav.abcdefgh.tmp"
            assert orphan.is_file() and orphan.stat().st_size == 0
        unrelated = root / "audio/units/.0001-9999-0001.wav.abcdefgh.tmp"
        unrelated.write_bytes(b"user data")
        provider = UnitFake()
        Pipeline(MockLLMProvider(), provider, output).resume_job(root)
        assert (sha256_file(first), first.stat().st_mtime_ns) == before
        assert not orphan.exists()
        assert unrelated.read_bytes() == b"user data"
        assert not list((root / "audio/units").glob(".bookcast-tmp-*.tmp"))
        manifest = load_manifest(root / "manifest.json")
        calls = [c for c in manifest.ai_calls if c.task == "tts:0001:0002-0001"]
        assert len(calls) == (2 if window == "running" else 1)
        assert calls[-1].status == "completed" and manifest.status == "completed"
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=10)
        process.stderr.close()


def test_cleanup_only_removes_owned_and_manifest_bound_legacy_units(tmp_path):
    root = Pipeline(MockLLMProvider(), UnitFake(), tmp_path / "out").generate(DEMO, minutes=1)
    manifest = load_manifest(root / "manifest.json")
    units = root / "audio/units"
    legacy = units / ".0001-0001-0001.wav.abcdefgh.tmp"
    owned = units / ".bookcast-tmp-manifest.json.abcdefgh.tmp"
    unrelated = units / ".9999-0001-0001.wav.abcdefgh.tmp"
    symlink = units / ".bookcast-tmp-other.json.abcdefgh.tmp"
    legacy.touch()
    owned.write_bytes(b"partial")
    unrelated.touch()
    outside = tmp_path / "outside"
    outside.write_text("keep")
    symlink.symlink_to(outside)

    removed = cleanup_orphan_temporary_artifacts(root, manifest)

    assert set(removed) == {legacy, owned}
    assert unrelated.exists() and symlink.is_symlink() and outside.read_text() == "keep"

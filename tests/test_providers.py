"""Offline fault injection against real persisted pipeline checkpoints."""

import ast
from datetime import datetime, timedelta
import io
import json
from pathlib import Path
import subprocess
import sys
from urllib.error import HTTPError, URLError
from unittest.mock import patch

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from bookcast.adapters.compatible import CompatibleLLMProvider, classify_http
from bookcast.cli import app
from bookcast.errors import BookCastError
from bookcast.models import Chapter, ChapterAnalysis
from bookcast.pipeline import Pipeline, load_manifest
from bookcast.prompts import analysis_prompt
from bookcast.provider_api import ErrorKind, ProviderError, FAILOVER_ERRORS
from bookcast.provider_chain import ProviderChain
from bookcast.provider_config import ProviderSpec, ProvidersConfig, load_config
from bookcast.provider_registry import default_registry
from bookcast.providers import MockLLMProvider, MockTTSProvider
from bookcast.storage import fingerprint, sha256_file, write_json


class RecordingLLM(MockLLMProvider):
    def __init__(self, name="A", failure=None, target=("analysis", "0007")):
        self.name, self.failure, self.target, self.calls = name, failure, target, []

    def generate_structured(self, prompt, response_model):
        data = json.loads(prompt)
        task = (data["operation"], data["chapter"]["id"])
        self.calls.append(task)
        if task == self.target and self.failure:
            raise self.failure
        return super().generate_structured(prompt, response_model)


@pytest.fixture
def book(tmp_path):
    source = tmp_path / "book.txt"
    source.write_text("\n".join(f"Chapter {n}\nThis is chapter {n}." for n in range(1, 9)), encoding="utf-8")
    return source, tmp_path / "output"


def completed_files(job, count=6):
    return {str(p): (p.stat().st_mtime_ns, sha256_file(p)) for folder, suffix in
            (("analysis", "json"), ("scripts", "json"), ("audio", "wav"))
            for n in range(1, count + 1) if (p := job / folder / f"{n:04}.{suffix}").exists()}


@pytest.mark.parametrize("failure", [None, *[ProviderError(kind) for kind in sorted(FAILOVER_ERRORS)]])
def test_chapter_seven_failover_and_idempotence(book, failure):
    source, output = book
    a, b = RecordingLLM(failure=failure), RecordingLLM("B")
    pipeline = Pipeline(ProviderChain([a, b]), MockTTSProvider(), output)
    job = pipeline.generate(source)
    manifest = load_manifest(job / "manifest.json")
    assert manifest.status == "completed" and manifest.schema_version == 2
    assert len(a.calls) == (13 if failure else 16)
    assert b.calls == ([(op, chapter) for chapter in ("0007", "0008") for op in ("analysis", "script")] if failure else [])
    for call in manifest.ai_calls:
        assert len(call.input_hash) == 64 and call.prompt_version and call.model
        assert datetime.fromisoformat(call.timestamp).utcoffset() == timedelta(0)
        if call.status == "completed":
            assert call.output_hash in call.artifacts.values()
            assert all(sha256_file(job / path) == digest for path, digest in call.artifacts.items())
    if failure:
        report = manifest.provider_status["llm:A"]
        assert report["last_error"] == failure.kind and report["retryable"]
        assert any(c.task == "analysis:0007" and c.status == "failed_retryable" for c in manifest.ai_calls)
    before = completed_files(job, 8)
    manifest_before = (job / "manifest.json").read_bytes()
    pipeline.generate(source, resume=True)
    assert completed_files(job, 8) == before
    assert (job / "manifest.json").read_bytes() == manifest_before
    assert len(a.calls) == (13 if failure else 16)


@pytest.mark.parametrize("failure,kind", [
    (ProviderError(ErrorKind.AUTH), ErrorKind.AUTH),
    (ValueError("secret-input"), ErrorKind.INPUT),
    (ProviderError(ErrorKind.SCHEMA), ErrorKind.SCHEMA),
    (RuntimeError("sk-test-DO-NOT-LOG"), ErrorKind.BUSINESS),
])
def test_permanent_errors_never_switch_and_never_log_raw_exception(book, failure, kind):
    source, output = book
    a, b = RecordingLLM(failure=failure, target=("analysis", "0001")), RecordingLLM("B")
    with pytest.raises(BookCastError):
        Pipeline(ProviderChain([a, b]), MockTTSProvider(), output).generate(source)
    raw = next(output.glob("*/manifest.json")).read_text()
    call = json.loads(raw)["ai_calls"][-1]
    assert call["status"] == "failed_permanent" and call["error"] == kind and not b.calls
    assert "secret-input" not in raw and "sk-test-DO-NOT-LOG" not in raw


@pytest.mark.parametrize("bad_result,kind", [("schema", ErrorKind.SCHEMA), ("identity", ErrorKind.BUSINESS)])
def test_invalid_model_output_is_not_hidden_by_failover(book, bad_result, kind):
    class Invalid(RecordingLLM):
        def generate_structured(self, prompt, response_model):
            if bad_result == "schema":
                return {"unexpected": "field"}
            return super().generate_structured(prompt, response_model).model_copy(update={"chapter_id": "wrong"})
    source, output = book
    backup = RecordingLLM("B")
    with pytest.raises(BookCastError):
        Pipeline(ProviderChain([Invalid(), backup]), MockTTSProvider(), output).generate(source)
    assert not backup.calls
    assert load_manifest(next(output.glob("*/manifest.json"))).ai_calls[-1].error == kind


def test_exhausted_chain_resumes_smallest_task_with_new_configuration(book):
    source, output = book
    a = RecordingLLM(failure=ProviderError(ErrorKind.QUOTA), target=("script", "0007"))
    b = RecordingLLM("B", ProviderError(ErrorKind.TIMEOUT), target=("script", "0007"))
    with pytest.raises(BookCastError):
        Pipeline(ProviderChain([a, b]), MockTTSProvider(), output).generate(source)
    job = next(output.iterdir())
    before = completed_files(job)
    seventh_analysis = (job / "analysis/0007.json").stat().st_mtime_ns
    replacement = RecordingLLM("replacement")
    with pytest.raises(BookCastError, match="--resume"):
        Pipeline(replacement, MockTTSProvider(), output).generate(source)
    Pipeline(replacement, MockTTSProvider(), output).generate(source, resume=True)
    assert replacement.calls == [("script", "0007"), ("analysis", "0008"), ("script", "0008")]
    assert before == completed_files(job)
    assert (job / "analysis/0007.json").stat().st_mtime_ns == seventh_analysis


@pytest.mark.parametrize("crash_after_completed", [False, True])
def test_hard_process_exit_and_resume(book, crash_after_completed):
    source, output = book
    script = '''
import os, sys, json
from pathlib import Path
from bookcast.pipeline import Pipeline, _Runner
from bookcast.providers import MockLLMProvider, MockTTSProvider
after = sys.argv[3] == "True"
class Crash(MockLLMProvider):
    name = "A"
    def generate_structured(self, prompt, response_model):
        data = json.loads(prompt)
        if not after and data["operation"] == "analysis" and data["chapter"]["id"] == "0007":
            os._exit(17)
        return super().generate_structured(prompt, response_model)
observe = _Runner.observe
def crash_observe(self, attempt):
    observe(self, attempt)
    if after and attempt.task == "analysis:0007" and attempt.status == "completed":
        os._exit(17)
_Runner.observe = crash_observe
Pipeline(Crash(), MockTTSProvider(), Path(sys.argv[2])).generate(Path(sys.argv[1]))
'''
    result = subprocess.run([sys.executable, "-c", script, str(source), str(output), str(crash_after_completed)],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 17, result.stderr
    job = next(output.iterdir())
    before = completed_files(job)
    manifest = load_manifest(job / "manifest.json")
    assert manifest.steps["analysis:0007"].status == "running"
    assert manifest.ai_calls[-1].status == ("completed" if crash_after_completed else "running")
    b = RecordingLLM("B")
    Pipeline(b, MockTTSProvider(), output).generate(source, resume=True)
    assert completed_files(job) == before
    expected = ([] if crash_after_completed else [("analysis", "0007")]) + [
        ("script", "0007"), ("analysis", "0008"), ("script", "0008")]
    assert b.calls == expected
    if not crash_after_completed:
        assert any(c.error == "interrupted" for c in load_manifest(job / "manifest.json").ai_calls)


def test_attempt_states_are_durable_before_invocation_and_completion(book):
    source, output = book
    states = []
    from bookcast.pipeline import _Runner
    original = _Runner.observe
    def observe(runner, attempt):
        original(runner, attempt)
        stored = load_manifest(runner.root / "manifest.json").ai_calls[-1]
        assert stored.status == attempt.status
        if stored.status == "completed":
            assert all(sha256_file(runner.root / p) == h for p, h in stored.artifacts.items())
        states.append(stored.status)
    with patch.object(_Runner, "observe", observe):
        Pipeline(RecordingLLM(), MockTTSProvider(), output).generate(source)
    assert states == [state for _ in range(24) for state in ("pending", "running", "completed")]


def test_tts_failover_keeps_prior_audio(book):
    class TTS(MockTTSProvider):
        def __init__(self, name, failing=False):
            self.name, self.failing, self.calls = name, failing, []
        def synthesize(self, script, destination):
            self.calls.append(script.chapter_id)
            if self.failing and script.chapter_id == "0007":
                raise ProviderError(ErrorKind.QUOTA)
            return super().synthesize(script, destination)
    source, output = book
    a, b = TTS("tts-a", True), TTS("tts-b")
    job = Pipeline(RecordingLLM(), ProviderChain([a, b]), output).generate(source)
    assert a.calls == [f"{n:04}" for n in range(1, 8)] and b.calls == ["0007", "0008"]
    assert load_manifest(job / "manifest.json").provider_status["tts:tts-a"]["quota_exhausted"]


def test_phase1_manifest_migration_reuses_verified_legacy_artifacts(book):
    source, output = book
    job = Pipeline(MockLLMProvider(), MockTTSProvider(), output).generate(source)
    manifest = load_manifest(job / "manifest.json")
    manifest.schema_version = 1
    manifest.ai_calls, manifest.provider_status = [], {}
    manifest.config = {"llm": MockLLMProvider.cache_key, "tts": MockTTSProvider.cache_key}
    for name, step in manifest.steps.items():
        if ":" not in name:
            continue
        stage, cid = name.split(":")
        chapter, analysis, script = [sha256_file(job / folder / f"{cid}.json") for folder in ("chapters", "analysis", "scripts")]
        inputs = {"analysis": {"chapter": chapter, "llm": manifest.config["llm"]},
                  "script": {"chapter": chapter, "analysis": analysis, "llm": manifest.config["llm"]},
                  "tts": {"script": script, "tts": manifest.config["tts"]}}[stage]
        step.fingerprint = fingerprint({"pipeline": "1", "step": name, "inputs": inputs})
    write_json(job / "manifest.json", manifest.model_dump())
    raw, before = (job / "manifest.json").read_bytes(), completed_files(job, 8)
    new = RecordingLLM("new")
    Pipeline(new, MockTTSProvider(), output).generate(source, resume=True)
    migrated = load_manifest(job / "manifest.json")
    assert not new.calls and not migrated.ai_calls and migrated.schema_version == 2
    assert all(step.legacy for name, step in migrated.steps.items() if ":" in name)
    assert completed_files(job, 8) == before
    assert (job / "manifest.v1.json").read_bytes() == raw


@pytest.mark.parametrize("status,code,kind", [(429, "insufficient_quota", ErrorKind.QUOTA),
    (429, "project_spend_limit_exceeded", ErrorKind.QUOTA), (429, None, ErrorKind.RATE_LIMIT),
    (503, None, ErrorKind.UNAVAILABLE), (504, None, ErrorKind.TIMEOUT),
    (401, None, ErrorKind.AUTH), (403, None, ErrorKind.AUTH), (400, None, ErrorKind.INPUT)])
def test_http_errors_are_classified_without_response_secrets(status, code, kind):
    error = classify_http(status, json.dumps({"error": {"code": code, "message": "sk-secret"}}).encode())
    assert error.kind == kind and "sk-secret" not in str(error)


def compatible_spec(**overrides):
    return ProviderSpec.model_validate({"name": "remote", "kind": "llm", "type": "openai-compatible",
                                       "model": "test-model", "base_url": "https://example.invalid/v1", **overrides})


def test_compatible_request_structured_output_and_health(monkeypatch):
    provider = CompatibleLLMProvider(compatible_spec(api_key_env="BOOKCAST_TEST_KEY"))
    monkeypatch.setenv("BOOKCAST_TEST_KEY", "sk-wire-only")
    chapter = Chapter(id="0001", title="test", text="text", source_locator="lines:1-2")
    expected = MockLLMProvider().analyze(chapter)
    payloads = [json.dumps({"choices": [{"message": {"content": expected.model_dump_json()}}]}).encode(),
                b'{"choices":[{"message":{"content":"hello"}}]}', b'{"data":[{"id":"test-model"}]}']
    with patch("bookcast.adapters.compatible.request.build_opener") as factory:
        factory.return_value.open.side_effect = [io.BytesIO(body) for body in payloads]
        assert provider.generate_structured(analysis_prompt(chapter), ChapterAnalysis) == expected
        assert provider.generate("hello") == "hello"
        assert provider.health_check().availability == "available"
        req = factory.return_value.open.call_args_list[0].args[0]
        assert req.get_header("Authorization") == "Bearer sk-wire-only"
        sent = json.loads(req.data)
        assert sent["response_format"] == {"type": "json_object"} and sent["model"] == "test-model"
        assert req.full_url == "https://example.invalid/v1/chat/completions"
        assert factory.return_value.open.call_args_list[-1].args[0].get_method() == "GET"
    assert "sk-wire-only" not in provider.last_status.model_dump_json()


@pytest.mark.parametrize("body", [b"not-json", b'{"choices":[]}', b'{"choices":[{"message":{"content":"{}"}}]}'])
def test_compatible_malformed_response_is_permanent(body):
    provider = CompatibleLLMProvider(compatible_spec())
    with patch("bookcast.adapters.compatible.request.build_opener") as factory:
        factory.return_value.open.return_value = io.BytesIO(body)
        with pytest.raises(ProviderError) as caught:
            provider.generate_structured("prompt", ChapterAnalysis)
    assert caught.value.kind == ErrorKind.SCHEMA
    assert provider.last_status.last_error == ErrorKind.SCHEMA


@pytest.mark.parametrize("failure,kind", [
    (URLError(TimeoutError("private")), ErrorKind.TIMEOUT), (URLError("private"), ErrorKind.UNAVAILABLE),
    (HTTPError("https://example.invalid", 429, "private", {}, io.BytesIO(b'{"error":{"type":"insufficient_quota"}}')), ErrorKind.QUOTA)])
def test_compatible_transport_failures(failure, kind):
    with patch("bookcast.adapters.compatible.request.build_opener") as factory:
        factory.return_value.open.side_effect = failure
        with pytest.raises(ProviderError) as caught:
            CompatibleLLMProvider(compatible_spec()).generate("prompt")
    assert caught.value.kind == kind and "private" not in str(caught.value)


def test_missing_key_does_not_connect(monkeypatch):
    monkeypatch.delenv("BOOKCAST_TEST_MISSING", raising=False)
    with patch("bookcast.adapters.compatible.request.build_opener") as factory:
        report = CompatibleLLMProvider(compatible_spec(api_key_env="BOOKCAST_TEST_MISSING")).health_check()
    assert report.authentication_error and not report.retryable
    factory.assert_not_called()


@pytest.mark.parametrize("overrides", [{"base_url": "https://user:secret@example.com/v1"},
    {"base_url": "https://example.com/v1?key=secret"}, {"base_url": "http://example.com/v1"},
    {"type": "local", "base_url": "https://example.com/v1"}, {"api_key": "secret"}])
def test_unsafe_config_rejected(overrides):
    with pytest.raises(ValidationError):
        compatible_spec(**overrides)


@pytest.mark.parametrize("updates", [{"llm_priority": ["missing"]}, {"llm_priority": ["mock", "mock"]},
    {"llm_priority": ["mock-tts"]}, {"failover_on": ["schema_error"]}])
def test_invalid_chains_rejected(tmp_path, monkeypatch, updates):
    monkeypatch.chdir(tmp_path)
    settings = load_config().model_dump()
    with pytest.raises(ValidationError):
        ProvidersConfig.model_validate({**settings, **updates})


def test_registry_extension_and_cli_commands(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    registry = default_registry()
    registry.register("llm", "custom", lambda spec: RecordingLLM(spec.name))
    assert registry.create(compatible_spec(type="custom")).name == "remote"
    local = registry.create(compatible_spec(type="local", base_url="http://127.0.0.1:8000/v1"))
    assert local.capabilities().local
    runner = CliRunner()
    result = runner.invoke(app, ["config", "providers"])
    assert result.exit_code == 0 and json.loads(result.stdout)["llm_priority"] == ["mock"]
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0 and json.loads(result.stdout)["ready"]
    source = tmp_path / "book.txt"
    source.write_text("Chapter 1\nExample.")
    result = runner.invoke(app, ["generate", str(source), "--provider", "auto"])
    assert result.exit_code == 0, result.output
    result = runner.invoke(app, ["generate", str(source), "--provider", "missing"])
    assert result.exit_code == 1


def test_config_error_never_prints_accidentally_pasted_key(tmp_path):
    path = tmp_path / "bad.toml"
    path.write_text('api_key = "sk-DO-NOT-LOG"')
    result = CliRunner().invoke(app, ["config", "providers", "--config", str(path)])
    assert result.exit_code == 1 and "sk-DO-NOT-LOG" not in result.output


def test_pipeline_has_no_concrete_provider_or_sdk_imports():
    import bookcast.pipeline as module
    tree = ast.parse(Path(module.__file__).read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            assert not any(part in {"adapters", "providers", "openai", "anthropic"} for part in (node.module or "").split("."))
        if isinstance(node, ast.Import):
            assert all(alias.name.split(".")[0] not in {"openai", "anthropic"} for alias in node.names)


def test_tertiary_takes_over_and_policy_can_disable_switching(book):
    source, output = book
    a = RecordingLLM("A", ProviderError(ErrorKind.QUOTA), ("analysis", "0001"))
    b = RecordingLLM("B", ProviderError(ErrorKind.TIMEOUT), ("analysis", "0001"))
    c = RecordingLLM("C")
    Pipeline(ProviderChain([a, b, c]), MockTTSProvider(), output).generate(source)
    assert len(a.calls) == len(b.calls) == 1 and len(c.calls) == 16
    a.calls.clear()
    c.calls.clear()
    with pytest.raises(BookCastError):
        Pipeline(ProviderChain([a, c], failover_on=[]), MockTTSProvider(), output.parent / "policy").generate(source)
    assert len(a.calls) == 1 and not c.calls


def test_invalid_tts_does_not_switch(book):
    class InvalidTTS(MockTTSProvider):
        name = "bad-tts"
        def synthesize(self, script, destination):
            destination.write_bytes(b"invalid audio")
    source, output = book
    with patch.object(MockTTSProvider, "synthesize") as backup:
        with pytest.raises(BookCastError):
            Pipeline(RecordingLLM(), ProviderChain([InvalidTTS(), MockTTSProvider()]), output).generate(source)
        backup.assert_not_called()
    assert load_manifest(next(output.glob("*/manifest.json"))).ai_calls[-1].error == "schema_error"


def test_doctor_reports_missing_environment_and_auth(tmp_path):
    settings = ProvidersConfig(providers=[compatible_spec(api_key_env="BOOKCAST_TEST_DOCTOR_MISSING"),
        ProviderSpec(name="mock-tts", kind="tts", type="mock", model="mock-tones-v1")],
        llm_priority=["remote"], tts_priority=["mock-tts"])
    with patch("bookcast.cli.load_config", return_value=settings), patch("bookcast.cli.shutil.which", return_value=None), \
            patch.dict("os.environ", {}, clear=True):
        result = CliRunner().invoke(app, ["doctor"])
    report = json.loads(result.stdout)
    assert result.exit_code == 1 and not report["ready"] and not report["environment"]["ffmpeg"]
    assert report["providers"][0]["authentication_error"] and report["chains"]["tts"]

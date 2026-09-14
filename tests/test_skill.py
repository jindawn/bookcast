"""Execute the skill's examples through Core; no agent or external service required."""

import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
from unittest.mock import patch

import pytest
from typer.main import get_command
from typer.testing import CliRunner

from bookcast.cli import app
from bookcast.pipeline import Pipeline
from bookcast.provider_api import ErrorKind, ProviderError
from bookcast.providers import MockLLMProvider
from bookcast.source_api import BookIdentity, EditionCandidate, SearchResult, SourceOffer
from bookcast.sources import GutenbergSourceProvider
from bookcast.storage import sha256_file


ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills/bookcast/SKILL.md"
BLOCKS = re.findall(r"```([^\n]*)\n(.*?)```", SKILL.read_text(encoding="utf-8"), re.S)
COMMANDS = [shlex.split(line) for language, body in BLOCKS
            for line in body.splitlines() if line.strip() and not line.lstrip().startswith("#")]
GENERATE = [cmd for cmd in COMMANDS if cmd[1] == "generate"]


def example(command):
    return next(cmd.copy() for cmd in COMMANDS if cmd[1] == command)


def invoke(command, **substitutions):
    argv = [str(substitutions.get(arg, arg)) for arg in command[1:]]
    return CliRunner().invoke(app, argv)


def snapshot(root):
    return {str(p.relative_to(root)): (sha256_file(p), p.stat().st_mtime_ns)
            for p in root.rglob("*") if p.is_file() and p.name != ".lock"}


@pytest.fixture
def local_book(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    source = tmp_path / "books/我的书.txt"
    source.parent.mkdir()
    source.write_text("Chapter 1\nA first point.\nChapter 2\nA second point.", encoding="utf-8")
    return source


def test_skill_bundle_contains_instructions_and_public_cli_calls_only():
    # There must be no hidden parser/downloader, executable helper, or second pipeline.
    files = {p.relative_to(SKILL.parent).as_posix() for p in SKILL.parent.rglob("*") if p.is_file()}
    assert files == {"SKILL.md"}
    assert BLOCKS and all(language == "sh" for language, _ in BLOCKS)
    assert COMMANDS
    for command in COMMANDS:
        assert command[0] == "bookcast", command
        assert not set(command) & {";", "&&", "||", "|", ">", ">>", "<"}, command


@pytest.mark.parametrize("command", COMMANDS, ids=lambda c: shlex.join(c))
def test_documented_arguments_are_accepted_by_real_cli(command):
    cli = get_command(app)
    assert command[1] in cli.commands
    # Parse with Click/Typer, including option types/ranges; don't merely match help text.
    with cli.commands[command[1]].make_context(command[1], command[2:]) as context:
        assert not context.args


@pytest.mark.parametrize("command", GENERATE, ids=lambda c: c[c.index("--mode") + 1])
def test_local_examples_use_pipeline_and_restore_without_new_calls(command, local_book):
    source = local_book
    # Keep shell metacharacters inside a single argument. No shell executes this title/path.
    unusual = source.with_name("$(touch UNEXPECTED) ; quoted ' book.txt")
    source.rename(unusual)
    with patch.object(Pipeline, "generate", autospec=True, side_effect=Pipeline.generate) as core:
        result = invoke(command, **{"books/我的书.txt": unusual})
    assert result.exit_code == 0, result.output
    assert core.call_count == 1 and core.call_args.args[1] == unusual
    assert not Path("UNEXPECTED").exists()
    output = Path(command[command.index("--output-dir") + 1])
    job = next(output.glob("*/manifest.json")).parent
    manifest = json.loads((job / "manifest.json").read_text())
    assert manifest["content_options"] == {
        "mode": command[command.index("--mode") + 1],
        "minutes": int(command[command.index("--minutes") + 1]),
    }
    assert manifest["ai_calls"] and (job / "podcast.mp3").stat().st_size > 0
    assert all(call["provider"].startswith("mock") for call in manifest["ai_calls"])
    before = snapshot(job)
    unusual.rename(unusual.with_suffix(".moved"))
    substitutions = {"JOB_ID": manifest["job_id"], "output/bookcast-skill": output}
    for name in ("jobs", "status", "resume", "retry"):
        result = invoke(example(name), **substitutions)
        assert result.exit_code == 0, result.output
        if name == "status":
            status = json.loads(result.stdout)
            assert status["effective_state"] == "SUCCEEDED" and status["integrity"] == "ok"
            assert status["directory"] == str(job.resolve())
            assert status["progress"]["remaining"] == 0
    assert snapshot(job) == before  # Includes manifest, attempts, logs and all artifacts.


@pytest.mark.parametrize("eligible", [True, False], ids=["eligible", "rejected"])
def test_title_example_keeps_selection_and_eligibility_inside_core(local_book, monkeypatch, eligible):
    candidates = [EditionCandidate(id=f"gutenberg:{n}", provider="gutenberg",
        identity=BookIdentity(title=f"Synthetic Wealth edition {n}", authors=["Smith"], language="en"))
        for n in (101, 102)]
    queries = []

    def search(self, title, *, author=None, language=None):
        queries.append((title, author, language))
        return SearchResult(candidates=candidates)

    def sources(self, candidate):
        return [SourceOffer(candidate_id=candidate.id, provider="gutenberg", format="txt",
            local_path=str(local_book), eligible=eligible, rights_category="public_domain" if eligible else "unknown",
            rights_statement="Synthetic test fixture; not a real catalog rights claim.", jurisdiction="US")]

    # Stub the source boundary only. Selection, import, parsing, eligibility and Pipeline are real Core.
    monkeypatch.setattr(GutenbergSourceProvider, "search", search)
    monkeypatch.setattr(GutenbergSourceProvider, "sources", sources)
    listing = next(c for c in COMMANDS if c[1:3] == ["acquire", "The Wealth of Nations"] and "--list" in c)
    result = invoke(listing)
    assert result.exit_code == 0 and len(json.loads(result.stdout)["candidates"]) == 2
    assert queries[-1] == ("The Wealth of Nations", "Smith", None)
    selected = next(c for c in COMMANDS if "EDITION_ID" in c)
    ambiguous = selected.copy()
    index = ambiguous.index("--edition")
    del ambiguous[index:index + 2]
    with patch.object(Pipeline, "generate", autospec=True, side_effect=Pipeline.generate) as core:
        result = invoke(ambiguous)
        assert result.exit_code == 1 and core.call_count == 0
        result = invoke(selected, EDITION_ID="gutenberg:102")
        if not eligible:
            assert result.exit_code == 1 and core.call_count == 0
            assert not Path("output/bookcast-skill").exists()
            return
        assert result.exit_code == 0, result.output
        assert core.call_count == 1
    payload = json.loads(result.stdout)
    assert payload["status"] == "parsed" and payload["candidate"]["id"] == "gutenberg:102"
    job = Path(payload["pipeline_job"])
    status = invoke(example("status"), JOB_ID=str(job))
    assert status.exit_code == 0 and json.loads(status.stdout)["effective_state"] == "SUCCEEDED"
    assert Path(payload["podcast"]).is_file()
    metadata = json.loads((job / "metadata.json").read_text())
    assert metadata["language"] == "en"  # Chinese output was not mapped to a Chinese-source filter.
    assert metadata["acquisition"]["candidate_id"] == "gutenberg:102"


def test_resume_example_does_not_bypass_permanent_error(local_book, monkeypatch):
    generate = MockLLMProvider.generate_structured

    def fail(self, prompt, response_model):
        raise ProviderError(ErrorKind.SCHEMA)

    monkeypatch.setattr(MockLLMProvider, "generate_structured", fail)
    assert invoke(GENERATE[0]).exit_code == 1
    job = next(Path("output/bookcast-skill").glob("*/manifest.json")).parent
    before = snapshot(job)
    status = json.loads(invoke(example("status"), JOB_ID=str(job)).stdout)
    assert status["effective_state"] == "FAILED_PERMANENT"
    assert invoke(example("resume"), JOB_ID=str(job)).exit_code == 1
    assert snapshot(job) == before
    monkeypatch.setattr(MockLLMProvider, "generate_structured", generate)
    assert invoke(example("retry"), JOB_ID=str(job)).exit_code == 0
    assert json.loads(invoke(example("status"), JOB_ID=str(job)).stdout)["effective_state"] == "SUCCEEDED"


def test_core_cli_operates_from_distribution_without_skill(tmp_path):
    # Copy only the application package, not repository docs, skills or test helpers.
    core = tmp_path / "core"
    shutil.copytree(ROOT / "src/bookcast", core / "bookcast", ignore=shutil.ignore_patterns("__pycache__"))
    assert not (tmp_path / "skills").exists()
    source = tmp_path / "example.txt"
    source.write_text("Chapter 1\nA standalone point.", encoding="utf-8")
    env = {**os.environ, "PYTHONPATH": str(core)}
    script = ("import pathlib, bookcast; "
              "assert pathlib.Path(bookcast.__file__).parent == pathlib.Path('core/bookcast').resolve(); "
              "from bookcast.cli import app; app()")

    def run(*args):
        result = subprocess.run([sys.executable, "-c", script, *args], cwd=tmp_path, env=env,
                                capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stdout + result.stderr
        return result

    run("--help")
    run("generate", str(source))
    listing = json.loads(run("jobs", "--json").stdout)
    job = listing["jobs"][0]
    before = snapshot(Path(job["directory"]))
    run("resume", job["job_id"])
    status = json.loads(run("status", job["job_id"], "--json").stdout)
    assert status["effective_state"] == "SUCCEEDED" and status["integrity"] == "ok"
    assert snapshot(Path(job["directory"])) == before

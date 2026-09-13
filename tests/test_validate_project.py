"""Offline regression checks, using disposable Git repositories and no user data."""

import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("validate_project", ROOT / "scripts/validate_project.py")
validator = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(validator)


class ValidationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        for name in validator.REQUIRED_FILES:
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("", encoding="utf-8")
        self.schema = json.loads((ROOT / "docs/STATE.schema.json").read_text(encoding="utf-8"))
        self.state = {
            "schema_version": 1, "project": "BookCast", "current_phase": "phase-0",
            "current_task": "Initialize handoff", "task_status": "completed", "branch": "main",
            "last_verified_commit": None, "completed_tasks": ["created handoff"],
            "in_progress": [], "next_actions": ["Start phase 1"], "tests": [],
            "known_failures": [], "blockers": [],
            "important_files": [{"path": "README.md", "purpose": "entry point"}],
            "updated_at": "2026-09-12T21:00:00Z",
        }
        self.git("init", "--initial-branch=main")
        self.git("config", "user.name", "BookCast Test")
        self.git("config", "user.email", "test@example.invalid")
        self.write()

    def git(self, *args):
        return subprocess.run(["git", "-c", "commit.gpgSign=false", "-c",
                               f"core.hooksPath={self.root / 'disabled-hooks'}",
                               "-C", str(self.root), *args], check=True,
                              capture_output=True, text=True).stdout.strip()

    def write(self):
        (self.root / "docs/STATE.json").write_text(json.dumps(self.state), encoding="utf-8")
        (self.root / "docs/STATE.schema.json").write_text(json.dumps(self.schema), encoding="utf-8")

    def errors(self):
        self.write()
        return "\n".join(validator.validate(self.root))

    def test_valid_initial_repository_without_head(self):
        self.assertEqual(self.errors(), "")

    def test_cli_success_and_failure_exit_codes(self):
        command = [sys.executable, str(ROOT / "scripts/validate_project.py"), "--root", str(self.root)]
        result = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("PASSED", result.stdout)
        (self.root / "README.md").unlink()
        result = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertIn("missing required file: README.md", result.stderr)

    def test_invalid_json_and_duplicate_keys(self):
        for content in ('{"project":', '{"project":"BookCast","project":"BookCast"}', '{"value":NaN}'):
            with self.subTest(content=content):
                (self.root / "docs/STATE.json").write_text(content, encoding="utf-8")
                self.assertIn("JSON:", "\n".join(validator.validate(self.root)))

    def test_required_field_and_types(self):
        del self.state["current_task"]
        self.assertIn("missing required field 'current_task'", self.errors())
        self.state["current_task"] = 1
        self.assertIn("$.current_task: expected type string", self.errors())
        self.state["schema_version"] = True
        self.assertIn("$.schema_version: expected type integer", self.errors())

    def test_unknown_field_and_duplicate_array(self):
        self.state["surprise"] = "value"
        self.state["completed_tasks"] *= 2
        errors = self.errors()
        self.assertIn("unexpected field 'surprise'", errors)
        self.assertIn("duplicate items", errors)

    def test_unknown_schema_keyword_is_rejected_even_in_unused_field(self):
        self.schema["properties"]["tests"]["items"]["properties"]["name"]["format"] = "email"
        self.assertIn("unsupported schema keyword 'format'", self.errors())

    def test_bad_schema_pattern(self):
        self.schema["properties"]["project"]["pattern"] = "["
        self.assertIn("invalid regular expression", self.errors())

    def test_timestamp_calendar_and_format(self):
        for value in ("2026-02-30T21:00:00Z", "2026-09-12T25:00:00Z", "2026-09-12T21:00:00+00:00"):
            with self.subTest(value=value):
                self.state["updated_at"] = value
                self.assertIn("$.updated_at:", self.errors())

    def test_paths_cannot_escape_or_use_absolute_paths(self):
        for path in ("../outside", str(self.root / "README.md"), "C:\\outside", "docs/../README.md"):
            with self.subTest(path=path):
                self.state["important_files"][0]["path"] = path
                self.assertIn("path must be repository-relative", self.errors())

    def test_symlink_escape_is_rejected(self):
        (self.root / "escape").symlink_to(self.root.parent, target_is_directory=True)
        self.state["important_files"][0]["path"] = "escape"
        self.assertIn("path escapes repository", self.errors())

    def test_missing_state_path_and_directory_support(self):
        self.state["important_files"][0]["path"] = "missing.md"
        self.assertIn("missing local path", self.errors())
        self.state["important_files"][0]["path"] = "docs/"
        self.assertEqual(self.errors(), "")

    def test_in_progress_paths_and_owner(self):
        self.state["task_status"] = "in_progress"
        self.assertIn("requires at least one task with an owner", self.errors())
        self.state["in_progress"] = [{"task": "work", "owner": "agent", "files": ["missing.py"]}]
        self.assertIn("$.in_progress[0].files[0]: missing local path", self.errors())
        self.state["in_progress"][0] = {"task": "work", "owner": " ", "files": ["docs/"]}
        self.assertIn("owner must not be blank", self.errors())

    def test_local_links_and_anchors(self):
        (self.root / "README.md").write_text(
            "[docs](docs/PRODUCT.md#scope) [anchor](#local) [web](https://example.invalid/a)\n"
            "[ref]: <docs/ROADMAP.md>\n", encoding="utf-8")
        (self.root / "docs/HANDOFF.md").write_text("[readme](../README.md)", encoding="utf-8")
        self.assertEqual(self.errors(), "")
        (self.root / "README.md").write_text("[missing](docs/missing.md)", encoding="utf-8")
        self.assertIn("README.md link: missing local path", self.errors())
        (self.root / "README.md").write_text("[escape](../outside)", encoding="utf-8")
        self.assertIn("README.md link: path escapes repository", self.errors())

    def test_task_status_contradictions(self):
        self.state["blockers"] = ["dependency unavailable"]
        self.assertIn("completed requires empty in_progress and blockers", self.errors())
        self.state["blockers"] = []
        self.state["in_progress"] = [{"task": "unfinished", "owner": "agent", "files": []}]
        self.assertIn("completed requires empty in_progress and blockers", self.errors())
        self.state["task_status"] = "blocked"
        self.assertIn("blocked requires at least one blocker", self.errors())

    def test_test_status_requires_consistent_timestamp(self):
        test = {"name": "check", "command": "python3 check.py", "status": "passed",
                "scope": "working_tree", "summary": "example", "checked_at": None}
        self.state["tests"] = [test]
        self.assertIn("passed/failed requires checked_at", self.errors())
        test["status"] = "not_run"
        test["checked_at"] = "2026-09-12T21:00:00Z"
        self.assertIn("not_run requires checked_at=null", self.errors())
        test["status"] = "passed"
        test["checked_at"] = "2026-02-30T21:00:00Z"
        self.assertIn("$.tests[0].checked_at: expected a real UTC timestamp", self.errors())

    def test_branch_and_missing_commit(self):
        self.state["branch"] = "wrong"
        self.state["last_verified_commit"] = "a" * 40
        errors = self.errors()
        self.assertIn("does not match the current Git branch", errors)
        self.assertIn("Git commit does not exist", errors)

    def test_commit_scope_requires_verified_commit(self):
        self.state["tests"] = [{
            "name": "check", "command": "python3 check.py", "status": "passed",
            "scope": "commit", "summary": "example", "checked_at": "2026-09-12T21:00:00Z",
        }]
        self.assertIn("commit scope requires last_verified_commit", self.errors())
        self.git("commit", "--allow-empty", "-m", "verified fixture")
        self.state["last_verified_commit"] = self.git("rev-parse", "HEAD")
        self.assertEqual(self.errors(), "")

    def test_verified_commit_must_be_on_current_history(self):
        self.git("add", ".")
        self.git("commit", "-m", "initial fixture")
        initial = self.git("rev-parse", "HEAD")
        self.state["last_verified_commit"] = initial
        self.assertEqual(self.errors(), "")
        self.git("checkout", "-b", "other")
        self.git("commit", "--allow-empty", "-m", "other history")
        other = self.git("rev-parse", "HEAD")
        self.git("checkout", "main")
        self.state["last_verified_commit"] = other
        self.assertIn("must be an ancestor of HEAD", self.errors())
        self.state["last_verified_commit"] = initial
        self.git("commit", "--allow-empty", "-m", "descendant")
        self.assertEqual(self.errors(), "")

    def test_git_object_must_be_commit(self):
        self.git("add", ".")
        self.git("commit", "-m", "initial fixture")
        self.state["last_verified_commit"] = self.git("rev-parse", "HEAD:README.md")
        self.assertIn("Git commit does not exist", self.errors())

    def test_schema_nullable_value(self):
        self.assertEqual(validator.value_errors(None, {"type": ["string", "null"], "pattern": "^a$"}), [])


if __name__ == "__main__":
    unittest.main()

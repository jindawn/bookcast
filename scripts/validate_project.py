#!/usr/bin/env python3
"""Read-only, offline checks for the BookCast repository handoff contract.

The schema checker deliberately implements only the keywords used by STATE's
schema, not the complete JSON Schema specification. Unknown keywords fail closed.
Markdown checking covers ordinary inline links and reference definitions, not a
complete CommonMark parser; URL fragments are not checked.
"""

from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path, PureWindowsPath
import re
import subprocess
import sys
from urllib.parse import unquote, urlsplit


REQUIRED_FILES = (
    "AGENTS.md", "CLAUDE.md", "README.md", "CONTEXT.md", "docs/PRODUCT.md",
    "docs/ARCHITECTURE.md", "docs/ROADMAP.md", "docs/DECISIONS.md",
    "docs/HANDOFF.md", "docs/WORKLOG.md", "docs/STATE.json",
    "docs/STATE.schema.json", "scripts/validate_project.py",
    "tests/test_validate_project.py", ".gitignore",
)
KEYWORDS = {
    "$schema", "title", "description", "type", "additionalProperties", "required",
    "properties", "const", "enum", "pattern", "minLength", "minItems",
    "uniqueItems", "items",
}
TYPES = {"object", "array", "string", "integer", "number", "boolean", "null"}
UTC_TIME = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$")
INLINE_LINK = re.compile(r"\[[^\]\n]*\]\(\s*(<[^>\n]+>|[^\s)]+)")
REFERENCE_LINK = re.compile(r"^\s{0,3}\[[^\]\n]+\]:\s*(<[^>\n]+>|\S+)", re.MULTILINE)


def schema_errors(schema: object, location: str = "$schema") -> list[str]:
    """Reject unsupported keywords or malformed schemas before checking data."""
    if not isinstance(schema, dict):
        return [f"{location}: schema must be an object"]
    errors = [f"{location}: unsupported schema keyword {key!r}" for key in schema.keys() - KEYWORDS]
    for key in ("$schema", "title", "description", "pattern"):
        if key in schema and not isinstance(schema[key], str):
            errors.append(f"{location}.{key}: must be a string")
    if "type" in schema:
        types = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
        if not types or any(not isinstance(t, str) or t not in TYPES for t in types):
            errors.append(f"{location}.type: unsupported type")
    for key in ("additionalProperties", "uniqueItems"):
        if key in schema and not isinstance(schema[key], bool):
            errors.append(f"{location}.{key}: only boolean values are supported")
    for key in ("minLength", "minItems"):
        if key in schema and (type(schema[key]) is not int or schema[key] < 0):
            errors.append(f"{location}.{key}: must be a nonnegative integer")
    if "required" in schema and (
        not isinstance(schema["required"], list)
        or any(not isinstance(key, str) for key in schema["required"])
    ):
        errors.append(f"{location}.required: must be an array of property names")
    if "enum" in schema and (not isinstance(schema["enum"], list) or not schema["enum"]):
        errors.append(f"{location}.enum: must be a nonempty array")
    if isinstance(schema.get("pattern"), str):
        try:
            re.compile(schema["pattern"])
        except re.error:
            errors.append(f"{location}.pattern: invalid regular expression")
    if "properties" in schema:
        if not isinstance(schema["properties"], dict):
            errors.append(f"{location}.properties: must be an object")
        else:
            for key, child in schema["properties"].items():
                errors.extend(schema_errors(child, f"{location}.properties.{key}"))
    if "items" in schema:
        errors.extend(schema_errors(schema["items"], f"{location}.items"))
    return errors


def matches_type(value: object, kind: str) -> bool:
    return {
        "object": isinstance(value, dict), "array": isinstance(value, list),
        "string": isinstance(value, str), "integer": type(value) is int,
        "number": type(value) in (int, float), "boolean": isinstance(value, bool),
        "null": value is None,
    }[kind]


def value_errors(value: object, schema: dict, location: str = "$") -> list[str]:
    """Validate data against an already checked schema from our supported subset."""
    errors: list[str] = []
    if "type" in schema:
        types = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
        if not any(matches_type(value, kind) for kind in types):
            return [f"{location}: expected type {' or '.join(types)}"]
    if "const" in schema and value != schema["const"]:
        errors.append(f"{location}: does not match const")
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{location}: not an allowed enum value")
    if isinstance(value, dict):
        for key in schema.get("required", []):
            if key not in value:
                errors.append(f"{location}: missing required field {key!r}")
        properties = schema.get("properties", {})
        for key, child in value.items():
            if key in properties:
                errors.extend(value_errors(child, properties[key], f"{location}.{key}"))
            elif schema.get("additionalProperties") is False:
                errors.append(f"{location}: unexpected field {key!r}")
    elif isinstance(value, list):
        if len(value) < schema.get("minItems", 0):
            errors.append(f"{location}: too few items")
        if schema.get("uniqueItems"):
            serialized = [json.dumps(item, sort_keys=True, ensure_ascii=False) for item in value]
            if len(set(serialized)) != len(serialized):
                errors.append(f"{location}: duplicate items")
        if "items" in schema:
            for index, child in enumerate(value):
                errors.extend(value_errors(child, schema["items"], f"{location}[{index}]"))
    elif isinstance(value, str):
        if len(value) < schema.get("minLength", 0):
            errors.append(f"{location}: string is too short")
        if "pattern" in schema and not re.search(schema["pattern"], value):
            errors.append(f"{location}: does not match required pattern")
    return errors


def load_json(path: Path) -> object:
    def reject_constant(value: str) -> None:
        raise ValueError(f"non-JSON number {value}")

    def reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict:
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate object key {key!r}")
            result[key] = value
        return result

    return json.loads(path.read_text(encoding="utf-8"), parse_constant=reject_constant,
                      object_pairs_hook=reject_duplicate_keys)


def time_errors(value: str | None, location: str) -> list[str]:
    if value is None:
        return []
    try:
        if not UTC_TIME.fullmatch(value):
            raise ValueError("format")
        datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return [f"{location}: expected a real UTC timestamp in YYYY-MM-DDTHH:MM:SSZ format"]
    return []


def path_errors(root: Path, raw: str, location: str, *, base: Path | None = None) -> list[str]:
    path = Path(raw)
    if (path.is_absolute() or PureWindowsPath(raw).drive or "\\" in raw
            or (base is None and ".." in path.parts)):
        return [f"{location}: path must be repository-relative without traversal"]
    target = ((base or root) / path).resolve()
    if not target.is_relative_to(root):
        return [f"{location}: path escapes repository"]
    if not target.exists():
        return [f"{location}: missing local path {raw!r}"]
    return []


def markdown_errors(root: Path) -> list[str]:
    errors = []
    # Only repository handoff documents; do not scan imported books or user data.
    documents = [root / name for name in REQUIRED_FILES if name.endswith(".md")]
    for path in documents:
        if not path.is_file():
            continue
        content = path.read_text(encoding="utf-8")
        for match in (*INLINE_LINK.finditer(content), *REFERENCE_LINK.finditer(content)):
            raw = match.group(1).strip("<>")
            try:
                url = urlsplit(raw)
            except ValueError:
                errors.append(f"{path.relative_to(root)}: malformed link")
                continue
            if url.scheme or url.netloc or not url.path:
                continue
            errors.extend(path_errors(root, unquote(url.path),
                                      f"{path.relative_to(root)} link", base=path.parent))
    return errors


def git_errors(root: Path, state: dict) -> list[str]:
    def git(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(["git", "-C", str(root), *args], capture_output=True,
                              text=True, check=False, timeout=10)

    try:
        repository = git("rev-parse", "--show-toplevel")
        if repository.returncode or Path(repository.stdout.strip()).resolve() != root:
            return ["Git: --root must be the root of an initialized Git repository"]
        branch = git("symbolic-ref", "--quiet", "--short", "HEAD")
        errors = []
        if branch.returncode:
            errors.append("Git: detached HEAD is unsupported; check out a named branch")
        elif branch.stdout.strip() != state["branch"]:
            errors.append("$.branch: does not match the current Git branch")
        commit = state["last_verified_commit"]
        if commit is not None:
            exists = git("cat-file", "-t", commit)
            if exists.returncode or exists.stdout.strip() != "commit":
                errors.append("$.last_verified_commit: Git commit does not exist")
            elif git("merge-base", "--is-ancestor", commit, "HEAD").returncode:
                errors.append("$.last_verified_commit: must be an ancestor of HEAD")
        return errors
    except (OSError, subprocess.TimeoutExpired):
        return ["Git: unable to run local Git checks (install Git and check the repository)"]


def validate(root: Path) -> list[str]:
    root = root.resolve()
    errors = [f"missing required file: {name}" for name in REQUIRED_FILES
              if not (root / name).is_file()]
    try:
        schema = load_json(root / "docs/STATE.schema.json")
        state = load_json(root / "docs/STATE.json")
    except (OSError, UnicodeError, ValueError) as exc:
        return errors + [f"JSON: cannot read valid STATE/schema JSON: {exc}"]
    contract_errors = schema_errors(schema)
    if contract_errors:
        return errors + contract_errors
    contract_errors = value_errors(state, schema)
    if contract_errors:
        return errors + contract_errors
    # The schema guarantees the shapes needed by the semantic checks below.
    errors.extend(time_errors(state["updated_at"], "$.updated_at"))
    for index, test in enumerate(state["tests"]):
        location = f"$.tests[{index}]"
        errors.extend(time_errors(test["checked_at"], location + ".checked_at"))
        if test["status"] == "not_run" and test["checked_at"] is not None:
            errors.append(f"{location}: not_run requires checked_at=null")
        if test["status"] in ("passed", "failed") and test["checked_at"] is None:
            errors.append(f"{location}: passed/failed requires checked_at")
        if test["scope"] == "commit" and state["last_verified_commit"] is None:
            errors.append(f"{location}: commit scope requires last_verified_commit")
    status = state["task_status"]
    if status == "completed" and (state["in_progress"] or state["blockers"]):
        errors.append("$.task_status: completed requires empty in_progress and blockers")
    if status == "in_progress" and not state["in_progress"]:
        errors.append("$.task_status: in_progress requires at least one task with an owner")
    if status == "blocked" and not state["blockers"]:
        errors.append("$.task_status: blocked requires at least one blocker")
    for index, entry in enumerate(state["important_files"]):
        errors.extend(path_errors(root, entry["path"], f"$.important_files[{index}].path"))
    for index, entry in enumerate(state["in_progress"]):
        if not entry["owner"].strip():
            errors.append(f"$.in_progress[{index}].owner: owner must not be blank")
        for file_index, raw in enumerate(entry["files"]):
            errors.extend(path_errors(root, raw, f"$.in_progress[{index}].files[{file_index}]"))
    try:
        errors.extend(markdown_errors(root))
    except (OSError, UnicodeError) as exc:
        errors.append(f"Markdown: cannot read handoff documents: {exc}")
    errors.extend(git_errors(root, state))
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1],
                        help="repository root (defaults to this script's repository)")
    args = parser.parse_args(argv)
    errors = validate(args.root)
    if errors:
        print(f"BookCast validation FAILED ({len(errors)} errors):", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    print("BookCast validation PASSED: required files, STATE schema/semantics, UTC times, local links and Git.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

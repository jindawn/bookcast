"""Atomic local storage and process locks; no stale lock after process death."""

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Iterator

from .errors import BookCastError


_BOOKCAST_TEMP = re.compile(r"^\.bookcast-tmp-.+\.[A-Za-z0-9_]{8}\.tmp$")
_UNIT_TASK = re.compile(r"^tts:([0-9]{4,}):([0-9]{4}-[0-9]{4})$")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fingerprint(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def artifact_path(root: Path, relative: str) -> Path:
    path = Path(relative)
    if path.is_absolute() or ".." in path.parts or "\\" in relative or not path.parts:
        raise BookCastError(f"非法产物相对路径：{relative}")
    target = root / path
    if not target.resolve().is_relative_to(root.resolve()):
        raise BookCastError(f"产物路径越过任务目录：{relative}")
    current = root
    for part in path.parts:
        current = current / part
        if current.is_symlink():
            raise BookCastError(f"产物路径不允许符号链接：{relative}")
    return target


@contextmanager
def atomic_target(path: Path) -> Iterator[Path]:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".bookcast-tmp-{path.name}.", suffix=".tmp", dir=path.parent)
    os.close(fd)
    temp = Path(temporary)
    try:
        yield temp
        with temp.open("rb") as stream:
            os.fsync(stream.fileno())
        os.replace(temp, path)
        if os.name == "posix":
            directory_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    finally:
        temp.unlink(missing_ok=True)


def cleanup_orphan_temporary_artifacts(root: Path, manifest=None) -> list[Path]:
    """Remove only BookCast atomic temps and zero-byte legacy speech-unit temps.

    Call while holding the job lock. Legacy names are constrained to unfinished
    or completed unit task IDs recorded in this job's manifest.
    """
    root = root.resolve()
    legacy_targets: set[str] = set()
    if manifest is not None:
        for task in manifest.steps:
            match = _UNIT_TASK.fullmatch(task)
            if match:
                chapter, unit = match.groups()
                legacy_targets.add(f".{chapter}-{unit}.wav")
    removed = []
    for current, directories, filenames in os.walk(root, topdown=True, followlinks=False):
        parent = Path(current)
        directories[:] = [name for name in directories if not (parent / name).is_symlink()]
        for name in filenames:
            candidate = parent / name
            if candidate.is_symlink() or not candidate.is_file():
                continue
            owned = _BOOKCAST_TEMP.fullmatch(name) is not None
            legacy_unit_temp = (
                manifest is not None
                and candidate.parent == root / "audio" / "units"
                and any(re.fullmatch(re.escape(target) + r"\.[A-Za-z0-9_]{8}\.tmp", name)
                        for target in legacy_targets)
                and candidate.stat().st_size == 0
            )
            if owned or legacy_unit_temp:
                candidate.unlink()
                removed.append(candidate)
    return removed


def write_json(path: Path, value: object) -> None:
    with atomic_target(path) as temporary:
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


@contextmanager
def job_lock(root: Path) -> Iterator[None]:
    path = artifact_path(root, ".lock")
    with path.open("a+b") as stream:
        if os.name == "nt":
            import msvcrt

            if path.stat().st_size == 0:
                stream.write(b"0")
                stream.flush()
            stream.seek(0)
            try:
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise BookCastError("该任务正被另一个进程使用，请稍后再试。") from exc
        else:
            import fcntl

            try:
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise BookCastError("该任务正被另一个进程使用，请稍后再试。") from exc
        try:
            yield
        finally:
            if os.name == "nt":
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def job_is_locked(root: Path) -> bool:
    """Read-only liveness probe. Kernel locks, not a PID or timeout, prove ownership."""
    path = artifact_path(root, '.lock')
    if not path.exists():
        return False
    # Do not create/change files during jobs/status. Windows needs a writable handle
    # for the byte-range lock but no data are written.
    with path.open('r+b' if os.name == 'nt' else 'rb') as stream:
        if os.name == 'nt':
            import msvcrt
            stream.seek(0)
            try:
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                return True
            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            try:
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return True
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
    return False

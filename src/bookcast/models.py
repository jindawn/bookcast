"""Versioned application data. Unrelated to docs/STATE.json's development state."""

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Chapter(Model):
    id: str = Field(pattern=r"^[0-9]{4,}$")
    title: str = Field(min_length=1)
    text: str = Field(min_length=1)
    source_locator: str = Field(min_length=1)


class BookMetadata(Model):
    schema_version: Literal[1] = 1
    book_id: str
    title: str
    authors: list[str] = Field(default_factory=list)
    language: str | None = None
    source_name: str
    source_sha256: str
    source_format: Literal["epub", "pdf", "txt"]
    chapter_ids: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    coverage: Literal["complete", "partial"] = "complete"


class NormalizedBook(Model):
    metadata: BookMetadata
    chapters: list[Chapter] = Field(min_length=1)


class ChapterAnalysis(Model):
    chapter_id: str
    summary: str = Field(min_length=1)
    key_points: list[str] = Field(min_length=1)
    source_locator: str
    is_mock: bool = True


class DialogueTurn(Model):
    speaker: Literal["主持人", "嘉宾"]
    text: str = Field(min_length=1)


class PodcastScript(Model):
    chapter_id: str
    title: str
    turns: list[DialogueTurn] = Field(min_length=2)
    source_locator: str
    is_mock: bool = True


class StepRecord(Model):
    status: Literal["running", "completed", "failed"]
    fingerprint: str
    artifacts: dict[str, str] = Field(default_factory=dict)
    attempts: int = 1
    error: str | None = None
    updated_at: str = Field(default_factory=utc_now)


class Manifest(Model):
    schema_version: Literal[1] = 1
    pipeline_version: Literal["1"] = "1"
    book_id: str
    source_sha256: str
    source_name: str
    source_format: Literal["epub", "pdf", "txt"]
    config: dict[str, str]
    status: Literal["pending", "running", "failed", "completed"] = "pending"
    steps: dict[str, StepRecord] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    error: str | None = None
    created_at: str = Field(default_factory=utc_now)
    updated_at: str = Field(default_factory=utc_now)

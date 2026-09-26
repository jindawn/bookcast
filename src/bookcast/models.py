"""Versioned application data. Unrelated to docs/STATE.json's development state."""

from datetime import datetime, timezone
from typing import Literal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator
from .generation import GenerationAudit, ProviderUsage


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SourceTextBlock(Model):
    """A piece of parsed text with a locator back to the supplied artifact."""

    text: str = Field(min_length=1)
    method: Literal["native", "ocr"]
    source_artifact: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    resource_locator: str = Field(min_length=1)
    page: int | None = Field(default=None, ge=1)
    region: tuple[float, float, float, float] | None = None
    region_space: Literal["page_display_normalized", "image_normalized"] | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)

    @model_validator(mode="after")
    def valid_provenance(self):
        if self.method == "ocr" and (self.region is None or self.region_space is None or self.confidence is None):
            raise ValueError("OCR 文本块必须包含区域、坐标空间和置信度。")
        if self.region is not None:
            x0, y0, x1, y1 = self.region
            if not (0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1):
                raise ValueError("来源文本块区域坐标无效。")
        return self


class DocumentExtractionInfo(Model):
    kind: Literal["text", "image", "mixed", "blank"]
    provider: str | None = None
    ocr_pages: list[int] = Field(default_factory=list)


class SourceFilterRecord(Model):
    unit: str
    classification: Literal['body', 'advertisement', 'table_of_contents', 'cover',
                            'publisher_notice', 'production_note', 'auxiliary']
    removed: bool
    reason: str
    matched_rule: str
    preview: str = Field(max_length=120)


class DocumentExtractionOptions(Model):
    mode: Literal["native", "auto"] = "native"
    provider: Literal["apple-vision"] | None = None

    @model_validator(mode="after")
    def validate_provider(self):
        if (self.mode == "auto") != (self.provider == "apple-vision"):
            raise ValueError("OCR auto 模式需要 apple-vision Provider；native 模式不能指定 OCR Provider。")
        return self


class Chapter(Model):
    id: str = Field(pattern=r"^[0-9]{4,}$")
    title: str = Field(min_length=1)
    text: str = Field(min_length=1)
    source_locator: str = Field(min_length=1)
    source_blocks: list[SourceTextBlock] = Field(default_factory=list)


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
    acquisition: dict | None = None
    document_extraction: DocumentExtractionInfo | None = None


class NormalizedBook(Model):
    metadata: BookMetadata
    chapters: list[Chapter] = Field(min_length=1)
    source_filter: list[SourceFilterRecord] = Field(default_factory=list)


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


class TaskState(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED_RETRYABLE = "FAILED_RETRYABLE"
    FAILED_PERMANENT = "FAILED_PERMANENT"
    SKIPPED = "SKIPPED"


def task_state(status: str, error_kind: str | None = None) -> TaskState:
    if status == "completed":
        return TaskState.SUCCEEDED
    if status == "failed":
        return TaskState.FAILED_RETRYABLE if error_kind in {
            'interrupted', 'timeout', 'quota_exhausted', 'rate_limit', 'temporary_unavailable'
        } else TaskState.FAILED_PERMANENT
    return TaskState(status.upper())


class ExecutionModel(Model):
    model_config = ConfigDict(extra="ignore")

    @model_validator(mode='before')
    @classmethod
    def read_state_projection(cls, data):
        # `status` is the compatibility storage field; state is its read-only enum projection.
        if isinstance(data, dict) and 'state' in data:
            data = {k: v for k, v in data.items() if k != 'state'}
        return data


class Artifact(Model):
    path: str
    sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    step: str
    input_hash: str
    prompt_version: str | None = None
    provider_config_hash: str | None = None
    provider: str | None = None
    model: str | None = None
    cache_key: str
    size_bytes: int = Field(ge=0)
    created_at: str = Field(default_factory=utc_now)


class RunOwner(Model):
    session_id: str
    pid: int
    hostname: str
    started_at: str = Field(default_factory=utc_now)


class Step(ExecutionModel):
    status: Literal["pending", "running", "completed", "failed", "failed_retryable", "failed_permanent", "skipped"] = "pending"
    fingerprint: str = ""
    input_hash: str | None = None
    artifacts: dict[str, str] = Field(default_factory=dict)
    attempts: int = 0
    error: str | None = None
    error_kind: str | None = None
    skip_reason: str | None = None
    legacy: bool = False
    updated_at: str = Field(default_factory=utc_now)
    @computed_field
    @property
    def state(self) -> TaskState:
        return task_state(self.status, self.error_kind)


class Attempt(ExecutionModel):
    id: str
    task: str
    kind: Literal["llm", "tts"]
    status: Literal["pending", "running", "completed", "failed_retryable", "failed_permanent"] = "pending"
    provider: str
    model: str
    prompt_version: str
    input_hash: str
    provider_config_hash: str | None = None
    generation: GenerationAudit | None = None
    provider_reported_usage: ProviderUsage | None = None
    reported_model: str | None = Field(default=None, pattern=r'^[a-zA-Z0-9_.:/-]{1,128}$')
    output_hash: str | None = None
    artifacts: dict[str, str] = Field(default_factory=dict)
    timestamp: str = Field(default_factory=utc_now)
    error: str | None = None
    retryable: bool = False
    # Diagnostics belong in events.jsonl. Do not extend manifest v3's wire
    # shape: older running Web processes reject unknown Attempt fields.
    error_type: str | None = Field(default=None, exclude=True)
    validation_field: str | None = Field(default=None, exclude=True)
    validation_reason: str | None = Field(default=None, exclude=True)
    finish_reason: str | None = Field(default=None, exclude=True)

    @computed_field
    @property
    def state(self) -> TaskState:
        return task_state(self.status)


class Job(ExecutionModel):
    schema_version: Literal[1, 2, 3] = 3
    job_id: str | None = None
    output_id: str | None = None
    cost_snapshot: dict | None = None
    source_path: str | None = None
    metadata_seed: BookMetadata | None = None
    provider_settings: dict | None = None
    extraction_options: DocumentExtractionOptions | None = None
    owner: RunOwner | None = None
    inventory_complete: bool = False
    error_kind: str | None = None
    artifact_records: dict[str, Artifact] = Field(default_factory=dict)
    pipeline_version: Literal["1", "2"] = "1"
    content_options: dict | None = None
    segment_revisions: dict[str, int] = Field(default_factory=dict)
    book_id: str
    source_sha256: str
    source_name: str
    source_format: Literal["epub", "pdf", "txt"]
    config: dict[str, str]
    status: Literal["pending", "running", "failed", "completed"] = "pending"
    steps: dict[str, Step] = Field(default_factory=dict)
    ai_calls: list[Attempt] = Field(default_factory=list)
    provider_status: dict[str, dict] = Field(default_factory=dict)
    legacy_config: dict[str, str] | None = None
    warnings: list[str] = Field(default_factory=list)
    error: str | None = None
    retry_after: float | None = None
    quota_reason: str | None = None
    created_at: str = Field(default_factory=utc_now)
    updated_at: str = Field(default_factory=utc_now)

    @computed_field
    @property
    def state(self) -> TaskState:
        return task_state(self.status, self.error_kind)


# Source-compatible imports for earlier phases; there is one durable job model.
StepRecord = Step
AIAttempt = Attempt
Manifest = Job

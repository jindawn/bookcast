"""Vendor-neutral contracts and safe, typed failure classification."""

from enum import StrEnum
import json
from pathlib import Path
from dataclasses import dataclass
from collections.abc import Callable
from typing import Literal, Protocol, TypeVar

from pydantic import BaseModel, Field, ValidationError, model_validator

from .errors import BookCastError
from .models import Model, PodcastScript
from .generation import GenerationAudit, ProviderUsage

T = TypeVar("T", bound=BaseModel)


class ErrorKind(StrEnum):
    RATE_LIMIT = "rate_limit"
    QUOTA = "quota_exhausted"
    UNAVAILABLE = "temporary_unavailable"
    TIMEOUT = "timeout"
    AUTH = "authentication_error"
    PERMISSION = "permission_denied"
    INPUT = "input_error"
    SCHEMA = "schema_error"
    BUSINESS = "business_error"
    INTERRUPTED = "interrupted"


FAILOVER_ERRORS = frozenset({ErrorKind.RATE_LIMIT, ErrorKind.QUOTA, ErrorKind.UNAVAILABLE, ErrorKind.TIMEOUT})


class ProviderError(BookCastError):
    """No upstream exception/body/header is interpolated into persisted errors."""

    def __init__(self, kind: ErrorKind, *, error_type: str | None = None,
                 validation_field: str | None = None, validation_reason: str | None = None):
        self.kind = ErrorKind(kind)
        self.error_type = error_type
        self.validation_field = validation_field
        self.validation_reason = validation_reason
        self.retryable = self.kind in FAILOVER_ERRORS or self.kind == ErrorKind.INTERRUPTED
        super().__init__(f"Provider 调用失败：{self.kind.value}")


def classify_error(exc: BaseException) -> ProviderError:
    if isinstance(exc, ProviderError):
        return exc
    if isinstance(exc, (KeyboardInterrupt, SystemExit)):
        return ProviderError(ErrorKind.INTERRUPTED)
    if isinstance(exc, TimeoutError):
        return ProviderError(ErrorKind.TIMEOUT)
    if isinstance(exc, ConnectionError):
        return ProviderError(ErrorKind.UNAVAILABLE)
    if isinstance(exc, (ValidationError, json.JSONDecodeError)):
        return ProviderError(ErrorKind.SCHEMA)
    if isinstance(exc, ValueError):
        return ProviderError(ErrorKind.INPUT)
    return ProviderError(ErrorKind.BUSINESS)


class ProviderStatus(Model):
    provider: str
    model: str
    availability: Literal["unknown", "available", "unavailable"] = "unknown"
    last_error: ErrorKind | None = None
    retryable: bool = False
    rate_limited: bool = False
    quota_exhausted: bool = False
    authentication_error: bool = False
    permission_denied: bool = False

    @classmethod
    def from_error(cls, provider: str, model: str, error: ProviderError) -> "ProviderStatus":
        return cls(provider=provider, model=model, availability="unavailable", last_error=error.kind,
                   retryable=error.retryable, rate_limited=error.kind == ErrorKind.RATE_LIMIT,
                   quota_exhausted=error.kind == ErrorKind.QUOTA, authentication_error=error.kind == ErrorKind.AUTH,
                   permission_denied=error.kind == ErrorKind.PERMISSION)


class ProviderCapabilities(Model):
    text: bool = False
    structured: bool = False
    speech: bool = False
    local: bool = False
    mock: bool = False
    speech_units: bool = False
    speech_segments: bool = False
    multi_speaker: bool = False
    cloud: bool = False


class SpeechUnit(Model):
    speaker: Literal["主持人", "嘉宾"]
    text: str = Field(min_length=1, max_length=80)


class SpeechInfo(Model):
    audio_kind: Literal["speech", "mock"]
    voice: str = Field(min_length=1, max_length=128)


class SpeechTurn(Model):
    speaker: Literal["主持人", "嘉宾"]
    text: str = Field(min_length=1, max_length=600)


class SpeechSegment(Model):
    turns: list[SpeechTurn] = Field(min_length=1, max_length=24)
    instruction: str | None = Field(default=None, max_length=512)

    @model_validator(mode="after")
    def bounded(self):
        if sum(len(t.text) for t in self.turns) > 600 or not any(t.text.strip() for t in self.turns):
            raise ValueError("speech segment exceeds text bounds or is empty")
        return self


class SegmentSpeechInfo(Model):
    audio_kind: Literal["speech"] = "speech"
    voices: dict[Literal["主持人", "嘉宾"], str] = Field(min_length=1, max_length=2)

    @model_validator(mode="after")
    def bounded_voices(self):
        if any(not 1 <= len(v) <= 128 for v in self.voices.values()):
            raise ValueError("invalid voice identity")
        return self


class Provider(Protocol):
    name: str
    model: str

    @property
    def cache_key(self) -> str: ...

    def health_check(self) -> ProviderStatus: ...

    def capabilities(self) -> ProviderCapabilities: ...


class LLMProvider(Provider, Protocol):
    def generate(self, prompt: str) -> str: ...

    def generate_structured(self, prompt: str, response_model: type[T]) -> T: ...


class TaskLLMProvider(LLMProvider, Protocol):
    generation_audit: GenerationAudit | None
    last_usage: ProviderUsage | None
    reported_model: str | None

    def for_task(self, task: str) -> LLMProvider:
        """Optional call-scoped view with effective config/cache key and response audit.

        Chains bind once per attempt. Providers without task settings need not implement it.
        """
        ...


class TTSProvider(Provider, Protocol):
    def synthesize(self, script: PodcastScript, destination: Path) -> None:
        """Write 24 kHz mono PCM16 WAV; invalid audio is a permanent failure."""
        ...


class UnitTTSProvider(TTSProvider, Protocol):
    def synthesize_unit(self, unit: SpeechUnit, destination: Path) -> SpeechInfo:
        """One bounded inference; enabled only by capabilities.speech_units."""
        ...


class SegmentTTSProvider(TTSProvider, Protocol):
    def synthesize_segment(self, segment: SpeechSegment, destination: Path) -> SegmentSpeechInfo:
        """One bounded request; Core owns segmentation, checkpoints and concatenation."""
        ...


@dataclass(frozen=True)
class ProviderRequestContext:
    """Call-scoped identity and destination for physical request diagnostics."""

    job_id: str | None
    output_id: str | None
    logical_chunk_id: str
    provider: str
    model: str
    telemetry_path: Path
    on_telemetry_degraded: Callable[[str], None] | None = None

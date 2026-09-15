"""Vendor-neutral contracts and safe, typed failure classification."""

from enum import StrEnum
import json
from pathlib import Path
from typing import Literal, Protocol, TypeVar

from pydantic import BaseModel, Field, ValidationError

from .errors import BookCastError
from .models import Model, PodcastScript

T = TypeVar("T", bound=BaseModel)


class ErrorKind(StrEnum):
    RATE_LIMIT = "rate_limit"
    QUOTA = "quota_exhausted"
    UNAVAILABLE = "temporary_unavailable"
    TIMEOUT = "timeout"
    AUTH = "authentication_error"
    INPUT = "input_error"
    SCHEMA = "schema_error"
    BUSINESS = "business_error"
    INTERRUPTED = "interrupted"


FAILOVER_ERRORS = frozenset({ErrorKind.RATE_LIMIT, ErrorKind.QUOTA, ErrorKind.UNAVAILABLE, ErrorKind.TIMEOUT})


class ProviderError(BookCastError):
    """No upstream exception/body/header is interpolated into persisted errors."""

    def __init__(self, kind: ErrorKind):
        self.kind = ErrorKind(kind)
        self.retryable = self.kind in FAILOVER_ERRORS or self.kind == ErrorKind.INTERRUPTED
        super().__init__(f"Provider 调用失败：{self.kind.value}")


def classify_error(exc: BaseException) -> ProviderError:
    if isinstance(exc, ProviderError):
        return ProviderError(exc.kind)
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

    @classmethod
    def from_error(cls, provider: str, model: str, error: ProviderError) -> "ProviderStatus":
        return cls(provider=provider, model=model, availability="unavailable", last_error=error.kind,
                   retryable=error.retryable, rate_limited=error.kind == ErrorKind.RATE_LIMIT,
                   quota_exhausted=error.kind == ErrorKind.QUOTA, authentication_error=error.kind == ErrorKind.AUTH)


class ProviderCapabilities(Model):
    text: bool = False
    structured: bool = False
    speech: bool = False
    local: bool = False
    mock: bool = False
    speech_units: bool = False


class SpeechUnit(Model):
    speaker: Literal["主持人", "嘉宾"]
    text: str = Field(min_length=1, max_length=80)


class SpeechInfo(Model):
    audio_kind: Literal["speech", "mock"]
    voice: str = Field(min_length=1, max_length=128)


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


class TTSProvider(Provider, Protocol):
    def synthesize(self, script: PodcastScript, destination: Path) -> None:
        """Write 24 kHz mono PCM16 WAV; invalid audio is a permanent failure."""
        ...


class UnitTTSProvider(TTSProvider, Protocol):
    def synthesize_unit(self, unit: SpeechUnit, destination: Path) -> SpeechInfo:
        """One bounded inference; enabled only by capabilities.speech_units."""
        ...

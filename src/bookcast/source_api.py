"""Book identity and source contracts, independent of catalogs and AI providers."""

from typing import Literal, Protocol
from pydantic import Field

from .models import Model, utc_now


class BookIdentity(Model):
    title: str = Field(min_length=1, max_length=2000)
    authors: list[str] = Field(default_factory=list)
    language: str | None = None
    isbn: str | None = None
    edition: str | None = None
    publication_year: int | None = None


class EditionCandidate(Model):
    id: str = Field(pattern=r"^[a-z0-9_-]+:[a-zA-Z0-9_-]+$")
    provider: str
    identity: BookIdentity
    metadata_url: str | None = None
    metadata_origin: Literal["catalog", "user"] = "catalog"
    # A catalog release is not the publication date of a print edition.
    catalog_release_date: str | None = None


class SearchResult(Model):
    candidates: list[EditionCandidate]
    complete: bool = True
    warnings: list[str] = Field(default_factory=list)


class SourceOffer(Model):
    candidate_id: str
    provider: str
    format: Literal["txt", "epub", "pdf"]
    url: str | None = None
    local_path: str | None = None
    rights_category: Literal["public_domain", "open_license", "official", "user_provided", "unknown"]
    eligible: bool
    rights_statement: str
    rights_url: str | None = None
    jurisdiction: str | None = None
    expected_book_id: int | None = None
    evidence_sha256: str | None = None


class BookSourceProvider(Protocol):
    name: str

    def search(self, title: str, *, author: str | None = None, language: str | None = None) -> SearchResult: ...

    def sources(self, candidate: EditionCandidate) -> list[SourceOffer]: ...


class DownloadReceipt(Model):
    url: str | None
    mime: str
    size: int = Field(gt=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class AcquisitionRecord(Model):
    schema_version: Literal[1] = 1
    id: str = Field(pattern=r"^[0-9a-f]{24}$")
    candidate: EditionCandidate
    source: SourceOffer
    status: Literal["pending", "downloading", "downloaded", "parsing", "parsed", "failed"] = "pending"
    download: DownloadReceipt | None = None
    artifacts: dict[str, str] = Field(default_factory=dict)
    error: str | None = None
    updated_at: str = Field(default_factory=utc_now)

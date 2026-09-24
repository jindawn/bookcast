"""Synthetic catalog and book fixtures; real public-book demo is separate."""

import csv
import hashlib
import io
import json
from pathlib import Path
import stat
from unittest.mock import patch
import zipfile

from ebooklib import epub
import pymupdf
import pytest
from typer.testing import CliRunner

from bookcast.acquisition import Acquirer, choose_edition
from bookcast.cli import app
from bookcast.errors import BookCastError
from bookcast.models import BookMetadata
from bookcast.pipeline import Pipeline, load_manifest
from bookcast.providers import MockLLMProvider, MockTTSProvider
from bookcast.source_api import BookIdentity, EditionCandidate, SearchResult, SourceOffer
from bookcast.source_validation import validate_source
from bookcast.sources import CATALOG_URL, MIRROR, GutenbergSourceProvider, UserSourceProvider, source_registry
from bookcast.storage import atomic_target, sha256_file

TEXT = b"[eBook #101]\nChapter 1\nSynthetic test book.\nChapter 2\nSecond chapter."


def catalog(rows):
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    writer.writerow(["Text#", "Type", "Issued", "Title", "Language", "Authors"])
    writer.writerows(rows)
    return stream.getvalue().encode()


def rdf(number=101, title="Test Wealth", rights="Public domain in the USA."):
    return f'''<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
        xmlns:pg="http://www.gutenberg.org/2009/pgterms/" xmlns:dc="http://purl.org/dc/terms/"
        xmlns:cc="http://web.resource.org/cc/">
      <cc:Work><cc:license rdf:resource="https://creativecommons.org/publicdomain/zero/1.0/"/></cc:Work>
      <pg:ebook rdf:about="ebooks/{number}"><dc:title>{title}</dc:title><dc:rights>{rights}</dc:rights>
        <dc:hasFormat><pg:file rdf:about="https://www.gutenberg.org/ebooks/{number}.txt.utf-8">
          <dc:format><rdf:Description><rdf:value>text/plain; charset=utf-8</rdf:value></rdf:Description></dc:format>
        </pg:file></dc:hasFormat>
      </pg:ebook></rdf:RDF>'''.encode()


class FakeHTTP:
    def __init__(self, resources):
        self.resources, self.calls = resources, []

    def fetch(self, url, *, max_bytes, allowed_mimes):
        self.calls.append(url)
        data, mime = self.resources[url]
        assert len(data) <= max_bytes and mime in allowed_mimes
        return data, {"url": url, "mime": mime, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}

    def download(self, url, destination, *, max_bytes, allowed_mimes, validate=None):
        data, receipt = self.fetch(url, max_bytes=max_bytes, allowed_mimes=allowed_mimes)
        with atomic_target(destination) as temporary:
            temporary.write_bytes(data)
            if validate:
                validate(temporary)
        return receipt


@pytest.fixture
def provider(tmp_path):
    http = FakeHTTP({CATALOG_URL: (catalog([
        [101, "Text", "2001-01-01", "Test Wealth", "en", "Example Author"],
        [102, "Text", "2002-01-01", "Test Wealth, volume 2", "en", "Example Author"],
        [103, "Text", "2003-01-01", "Test Wealth", "fr", "Other Author"],
    ]), "text/csv"), f"{MIRROR}/cache/epub/101/pg101.rdf": (rdf(), "application/rdf+xml"),
        f"{MIRROR}/cache/epub/101/pg101.txt": (TEXT, "text/plain")})
    return GutenbergSourceProvider(tmp_path / "cache", http), http


def test_identity_search_preserves_editions_and_unknown_publication_fields(provider):
    source, http = provider
    result = source.search("wealth")
    assert [c.id for c in result.candidates] == ["gutenberg:101", "gutenberg:102", "gutenberg:103"]
    identity = result.candidates[0].identity
    assert identity.publication_year is identity.isbn is identity.edition is None
    assert result.candidates[0].catalog_release_date == "2001-01-01"
    filtered = source.search("TEST wealth", author="other", language="fr")
    assert [c.id for c in filtered.candidates] == ["gutenberg:103"]
    assert http.calls.count(CATALOG_URL) == 1
    source.refresh = True
    source.search("wealth")
    assert http.calls.count(CATALOG_URL) == 2


def test_multiple_candidates_require_explicit_id(provider):
    source, _ = provider
    result = source.search("wealth")
    with pytest.raises(BookCastError, match="多个"):
        choose_edition(result)
    assert choose_edition(result, "gutenberg:101").identity.title == "Test Wealth"
    with pytest.raises(BookCastError):
        choose_edition(result, "../../101")
    with pytest.raises(BookCastError):
        choose_edition(SearchResult(candidates=[result.candidates[0]], complete=False))
    with pytest.raises(BookCastError):
        choose_edition(SearchResult(candidates=[]))


def test_catalog_truncation_is_explicit_and_blocks_automatic_selection(tmp_path):
    http = FakeHTTP({CATALOG_URL: (catalog([[i, "Text", "", "Common title", "en", "Author"] for i in range(1, 53)]), "text/csv")})
    result = GutenbergSourceProvider(tmp_path / "cache", http).search("Common")
    assert len(result.candidates) == 50 and not result.complete
    with pytest.raises(BookCastError):
        choose_edition(result)


@pytest.mark.parametrize("rights", ["Copyrighted. Read the copyright notice inside this book for details.", "", "CC0 metadata only"])
def test_metadata_cc0_never_authorizes_copyrighted_or_unknown_book(provider, rights, tmp_path):
    source, http = provider
    candidate = source.search("wealth").candidates[0]
    http.resources[candidate.metadata_url] = (rdf(rights=rights), "application/rdf+xml")
    offer = source.sources(candidate)[0]
    assert not offer.eligible and offer.rights_category == "unknown"
    with pytest.raises(BookCastError):
        Acquirer(tmp_path / "imports", http).acquire(candidate, offer)
    assert not any(url.endswith(".txt") for url in http.calls)


@pytest.mark.parametrize("body", [rdf(number=999), rdf(title="Wrong edition"), b"<!DOCTYPE x><rdf/>", b"invalid"])
def test_wrong_rdf_identity_or_unsafe_xml_rejected(provider, body):
    source, http = provider
    candidate = source.search("wealth").candidates[0]
    http.resources[candidate.metadata_url] = (body, "application/rdf+xml")
    with pytest.raises(BookCastError):
        source.sources(candidate)


def test_public_source_acquisition_checkpoint_and_pipeline_bridge(provider, tmp_path):
    source, http = provider
    candidate = choose_edition(source.search("wealth"), "gutenberg:101")
    offer = source.sources(candidate)[0]
    assert offer.eligible and offer.jurisdiction == "US" and offer.evidence_sha256
    importer = Acquirer(tmp_path / "imports", http)
    job = importer.acquire(candidate, offer)
    before = {str(p): (p.stat().st_mtime_ns, p.read_bytes()) for p in job.rglob("*") if p.is_file() and p.name != ".lock"}
    assert importer.acquire(candidate, offer) == job
    assert before == {str(p): (p.stat().st_mtime_ns, p.read_bytes()) for p in job.rglob("*") if p.is_file() and p.name != ".lock"}
    assert http.calls.count(offer.url) == 1
    metadata = BookMetadata.model_validate_json((job / "metadata.json").read_text())
    assert metadata.acquisition["identity"]["title"] == candidate.identity.title
    audio = Pipeline(MockLLMProvider(), MockTTSProvider(), tmp_path / "output").generate(job / "source/input.txt", metadata_seed=metadata)
    generated = BookMetadata.model_validate_json((audio / "metadata.json").read_text())
    assert generated.acquisition == metadata.acquisition and generated.title == "Test Wealth"
    assert (audio / "podcast.mp3").stat().st_size > 0


def test_parse_failure_resume_does_not_download_again(provider, tmp_path):
    source, http = provider
    candidate = source.search("wealth").candidates[0]
    offer = source.sources(candidate)[0]
    importer = Acquirer(tmp_path / "imports", http)
    with patch("bookcast.acquisition.parse_book", side_effect=BookCastError("parse failure")):
        with pytest.raises(BookCastError):
            importer.acquire(candidate, offer)
    record_path = next((tmp_path / "imports").glob("*/acquisition.json"))
    assert json.loads(record_path.read_text())["status"] == "failed"
    job = importer.acquire(candidate, offer)
    assert json.loads((job / "acquisition.json").read_text())["status"] == "parsed"
    assert http.calls.count(offer.url) == 1


def test_damaged_normalized_chapter_reparsed_without_download(provider, tmp_path):
    source, http = provider
    candidate = source.search("wealth").candidates[0]
    offer = source.sources(candidate)[0]
    importer = Acquirer(tmp_path / "imports", http)
    job = importer.acquire(candidate, offer)
    (job / "chapters/0001.json").write_text("corrupt")
    importer.acquire(candidate, offer)
    assert json.loads((job / "chapters/0001.json").read_text())["id"] == "0001"
    assert http.calls.count(offer.url) == 1


def test_gutenberg_body_id_must_match_catalog(provider, tmp_path):
    source, http = provider
    candidate = source.search("wealth").candidates[0]
    offer = source.sources(candidate)[0]
    http.resources[offer.url] = (TEXT.replace(b"#101", b"#999"), "text/plain")
    with pytest.raises(BookCastError, match="编号"):
        Acquirer(tmp_path / "imports", http).acquire(candidate, offer)
    assert not list((tmp_path / "imports").glob("*/source/input.txt"))


def test_user_url_requires_rights_assertion_and_preserves_origin(tmp_path):
    source = UserSourceProvider(url="https://example.org/book.txt", fmt="txt")
    candidate = source.search("User title").candidates[0]
    assert candidate.metadata_origin == "user"
    http = FakeHTTP({source.url: (b"Chapter 1\nOwn book.", "text/plain")})
    with pytest.raises(BookCastError):
        Acquirer(tmp_path, http).acquire(candidate, source.sources(candidate)[0])
    assert not http.calls
    source.rights_confirmed = True
    job = Acquirer(tmp_path, http).acquire(candidate, source.sources(candidate)[0])
    assert json.loads((job / "metadata.json").read_text())["acquisition"]["metadata_origin"] == "user"


@pytest.mark.parametrize("fmt", ["txt", "epub", "pdf"])
def test_local_import_formats_and_original_file_preserved(tmp_path, fmt):
    path = tmp_path / f"original.{fmt}"
    if fmt == "txt":
        path.write_bytes(b"Chapter 1\nOwn book.")
    elif fmt == "epub":
        book = epub.EpubBook()
        book.set_identifier("synthetic")
        book.set_title("Synthetic EPUB")
        book.set_language("en")
        page = epub.EpubHtml(title="One", file_name="one.xhtml", lang="en")
        page.content = "<h1>Chapter 1</h1><p>Own book.</p>"
        book.add_item(page)
        book.spine = [page]
        book.add_item(epub.EpubNcx())
        book.add_item(epub.EpubNav())
        epub.write_epub(str(path), book)
    else:
        with pymupdf.open() as book:
            page = book.new_page()
            page.insert_text((72, 72), "Own book.")
            book.save(path)
    digest = sha256_file(path)
    provider = UserSourceProvider(local_path=path, fmt=fmt)
    candidate = provider.search("../unsafe filename; book").candidates[0]
    job = Acquirer(tmp_path / "imports").acquire(candidate, provider.sources(candidate)[0])
    assert sha256_file(path) == digest and sha256_file(job / f"source/input.{fmt}") == digest
    assert job.parent == tmp_path / "imports" and len(job.name) == 24


def test_source_registry_extends_without_changing_acquirer():
    registry = source_registry()
    registry.register("custom", lambda **kwargs: "registered")
    assert registry.create("custom") == "registered"
    with pytest.raises(BookCastError):
        registry.register("custom", lambda: None)


@pytest.mark.parametrize("name", ["../escape.xhtml", "/absolute.xhtml", "C:/escape.xhtml", "folder\\escape.xhtml"])
def test_epub_traversal_rejected(tmp_path, name):
    path = tmp_path / "malicious.epub"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip")
        archive.writestr("META-INF/container.xml", "<container/>")
        archive.writestr(name, "<p>content</p>")
    with pytest.raises(BookCastError):
        validate_source(path, "epub", 1024 * 1024)


@pytest.mark.parametrize("attack", ["symlink", "bomb", "entity", "utf16-entity", "disguised-entity"])
def test_epub_symlink_zip_bomb_and_entities_rejected(tmp_path, attack):
    path = tmp_path / "malicious.epub"
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("mimetype", "application/epub+zip")
        archive.writestr("META-INF/container.xml", "<container/>")
        if attack == "symlink":
            entry = zipfile.ZipInfo("link.xhtml")
            entry.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(entry, "/etc/passwd")
        elif attack == "bomb":
            archive.writestr("huge.txt", b"a" * 1000000)
        else:
            text = '<!DOCTYPE x [<!ENTITY ex SYSTEM "file:///etc/passwd">]><x>&ex;</x>'
            archive.writestr("body.dat" if attack == "disguised-entity" else "body.xhtml",
                             text.encode("utf-16" if attack == "utf16-entity" else "utf-8"))
    with pytest.raises(BookCastError):
        validate_source(path, "epub", 1024 * 1024)


def unsafe_epub(path):
    book = epub.EpubBook()
    book.set_identifier("audit-unsafe-epub")
    book.set_title("Audit fixture")
    book.set_language("en")
    page = epub.EpubHtml(title="One", file_name="one.xhtml", lang="en")
    page.content = "<h1>Chapter 1</h1><p>A concrete idea about trade.</p>"
    book.add_item(page)
    book.spine = [page]
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    epub.write_epub(str(path), book)
    # The extra member does not affect ordinary EPUB parsing, so a parser-only
    # check would silently accept this unsafe archive on the primary CLI path.
    with zipfile.ZipFile(path, "a") as archive:
        archive.writestr("../escape.xhtml", "<p>outside</p>")


def test_direct_generate_rejects_unsafe_epub_before_parsing(tmp_path):
    path = tmp_path / "unsafe.epub"
    unsafe_epub(path)
    with pytest.raises(BookCastError, match="EPUB"):
        Pipeline(MockLLMProvider(), MockTTSProvider(), tmp_path / "output").generate(path)
    assert not list((tmp_path / "output").glob("*/podcast.mp3"))


def test_pre_fix_completed_parse_cannot_skip_source_validation_on_resume(tmp_path):
    path = tmp_path / "unsafe.epub"
    unsafe_epub(path)
    pipeline = Pipeline(MockLLMProvider(), MockTTSProvider(), tmp_path / "output")
    # Simulate a completed job created before source validation covered direct
    # generation. Its parse result and audio are valid according to old hashes.
    with patch("bookcast.pipeline.validate_source"):
        job = pipeline.generate(path)
    assert load_manifest(job / "manifest.json").steps["parse"].status == "completed"
    with pytest.raises(BookCastError, match="EPUB"):
        pipeline.generate(path, resume=True)


def test_direct_generate_rejects_source_over_100_mib_before_import(tmp_path):
    path = tmp_path / "oversized.txt"
    with path.open("wb") as stream:
        stream.truncate(100 * 1024 * 1024 + 1)
    with pytest.raises(BookCastError, match="大小|上限"):
        Pipeline(MockLLMProvider(), MockTTSProvider(), tmp_path / "output").generate(path)
    assert not list((tmp_path / "output").glob("*/source/input.txt"))


@pytest.mark.parametrize("body,fmt", [(b"<html>Login</html>", "txt"), (b"binary\x00", "txt"),
    (b"\xffinvalid", "txt"), (b"not a pdf", "pdf"), (b"not a zip", "epub")])
def test_invalid_file_content_rejected(tmp_path, body, fmt):
    path = tmp_path / "source"
    path.write_bytes(body)
    with pytest.raises(BookCastError):
        validate_source(path, fmt, 1024)


def test_output_symlink_cannot_escape_acquisition_directory(provider, tmp_path):
    source, http = provider
    candidate = source.search("wealth").candidates[0]
    offer = source.sources(candidate)[0]
    importer = Acquirer(tmp_path / "imports", http)
    job = importer.acquire(candidate, offer)
    chapter = job / "chapters/0001.json"
    # Replace only this test's generated fixture, not any user data.
    chapter.unlink()
    outside = tmp_path / "outside.json"
    outside.write_text("preserve")
    chapter.symlink_to(outside)
    with pytest.raises(BookCastError):
        importer.acquire(candidate, offer)
    assert outside.read_text() == "preserve"


def test_cli_ambiguity_does_not_search_or_download_source(provider, tmp_path):
    source, http = provider
    with patch("bookcast.sources.source_registry") as registry:
        registry.return_value.create.return_value = source
        result = CliRunner().invoke(app, ["acquire", "wealth", "--output-dir", str(tmp_path / "imports")])
    assert result.exit_code == 1 and len(json.loads(result.stdout)["candidates"]) == 3
    assert http.calls == [CATALOG_URL]


def test_cli_local_file_generate_and_resume(tmp_path):
    path = tmp_path / "own.txt"
    path.write_text("Chapter 1\nOwn work.")
    args = ["acquire", "Own book", "--file", str(path), "--output-dir", str(tmp_path / "imports"),
            "--generate", "--pipeline-output-dir", str(tmp_path / "audio")]
    runner = CliRunner()
    result = runner.invoke(app, args)
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data["status"] == "parsed" and Path(data["podcast"]).is_file()
    manifest = Path(data["pipeline_job"]) / "manifest.json"
    before = manifest.read_bytes()
    result = runner.invoke(app, [*args, "--resume"])
    assert result.exit_code == 0, result.output
    assert manifest.read_bytes() == before


def test_local_file_changed_after_identity_hash_is_not_published(tmp_path):
    path = tmp_path / "own.txt"
    path.write_text("Original text.")
    provider = UserSourceProvider(local_path=path, fmt="txt")
    candidate = provider.search("Own work").candidates[0]
    changed = False
    def change_after_hash(target):
        nonlocal changed
        digest = sha256_file(target)
        if target == path and not changed:
            changed = True
            path.write_text("New content after identity was calculated.")
        return digest
    with patch("bookcast.acquisition.sha256_file", side_effect=change_after_hash):
        with pytest.raises(BookCastError, match="变化"):
            Acquirer(tmp_path / "imports").acquire(candidate, provider.sources(candidate)[0])
    assert not list((tmp_path / "imports").glob("*/source/input.txt"))


def test_malformed_checkpoint_is_rejected_without_overwriting(provider, tmp_path):
    source, http = provider
    candidate = source.search("wealth").candidates[0]
    offer = source.sources(candidate)[0]
    importer = Acquirer(tmp_path / "imports", http)
    job = importer.acquire(candidate, offer)
    path = job / "acquisition.json"
    data = json.loads(path.read_text())
    data["download"] = {"bad": "sk-do-not-log"}
    path.write_text(json.dumps(data))
    before = path.read_bytes()
    with pytest.raises(BookCastError, match="记录无效") as exc:
        importer.acquire(candidate, offer)
    assert path.read_bytes() == before and "sk-do-not-log" not in str(exc.value)


def test_unknown_rights_cannot_be_enabled_by_eligibility_flag(provider, tmp_path):
    source, http = provider
    candidate = source.search("wealth").candidates[0]
    offer = source.sources(candidate)[0].model_copy(update={"rights_category": "unknown", "eligible": True})
    with pytest.raises(BookCastError):
        Acquirer(tmp_path / "imports", http).acquire(candidate, offer)
    assert not any(url.endswith(".txt") for url in http.calls)

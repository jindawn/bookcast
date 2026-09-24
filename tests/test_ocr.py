"""Selective local OCR, document classification, and source provenance."""

import json
import os
from pathlib import Path
import sys
from unittest.mock import patch

from ebooklib import epub
import pymupdf
import pytest
from typer.testing import CliRunner

from bookcast.cli import app
from bookcast.document_extraction import OCRText, inspect_pdf_isolated
from bookcast.models import BookMetadata, DocumentExtractionOptions
from bookcast.parsers import parse_epub, parse_pdf
from bookcast.pdf_extraction import inspect_pdf
from bookcast.pipeline import Pipeline, load_manifest
from bookcast.providers import MockLLMProvider, MockTTSProvider
from bookcast.storage import sha256_file


TEXT = "中文测试 2026 ABC"


class FakeOCR:
    name = "fake-ocr"
    cache_key = "fake-ocr-v1"

    def __init__(self, *, confidence=0.9):
        self.calls = 0
        self.confidence = confidence

    def recognize(self, png: bytes):
        assert png.startswith(b"\x89PNG\r\n\x1a\n")
        self.calls += 1
        return [OCRText(text="扫描页的中文观点和英文 ABC 示例。", region=(0.1, 0.2, 0.8, 0.4),
                        confidence=self.confidence)]


def _metadata(path: Path, fmt: str) -> BookMetadata:
    return BookMetadata(book_id="test", title="测试书", source_name=path.name,
                        source_sha256=sha256_file(path), source_format=fmt)


def _picture() -> bytes:
    source = pymupdf.open()
    page = source.new_page(width=500, height=300)
    page.insert_text((30, 100), TEXT, fontname="china-s", fontsize=24)
    return page.get_pixmap(matrix=pymupdf.Matrix(2, 2)).tobytes("png")


def _pdf(path: Path, kinds: list[str], *, rotation=0) -> None:
    document = pymupdf.open()
    image = _picture()
    for kind in kinds:
        page = document.new_page(width=500, height=300)
        if kind in {"text", "mixed"}:
            page.insert_text((30, 38), "Native heading about reading", fontsize=14)
        if kind in {"image", "mixed"}:
            rect = pymupdf.Rect(0, 55, 500, 300) if kind == "mixed" else page.rect
            page.insert_image(rect, stream=image)
        if rotation:
            page.set_rotation(rotation)
    document.save(path)
    document.close()


def test_detects_text_image_mixed_and_blank_before_ocr(tmp_path):
    source = tmp_path / "mixed.pdf"
    _pdf(source, ["text", "image", "mixed", "blank"])
    report = inspect_pdf(source)
    assert report["kind"] == "mixed"
    assert [page["kind"] for page in report["pages"]] == ["text", "image", "mixed", "blank"]
    result = CliRunner().invoke(app, ["inspect-document", str(source)])
    assert result.exit_code == 0
    assert json.loads(result.stdout)["kind"] == "mixed"
    assert inspect_pdf_isolated(source)["pages"] == report["pages"]


def test_text_pdf_never_invokes_ocr_and_blank_page_warns(tmp_path):
    source = tmp_path / "text.pdf"
    _pdf(source, ["text", "blank"])
    ocr = FakeOCR()
    parsed = parse_pdf(source, _metadata(source, "pdf"), ocr)
    assert ocr.calls == 0
    assert parsed.metadata.document_extraction.kind == "text"
    assert parsed.metadata.document_extraction.ocr_pages == []
    assert parsed.chapters[0].source_blocks[0].method == "native"
    assert any("第 2 页无可用文字" in warning for warning in parsed.metadata.warnings)


def test_scanned_pdf_ocr_has_page_region_artifact_and_confidence_warning(tmp_path):
    source = tmp_path / "scan.pdf"
    _pdf(source, ["image"])
    ocr = FakeOCR(confidence=0.3)
    parsed = parse_pdf(source, _metadata(source, "pdf"), ocr)
    block = parsed.chapters[0].source_blocks[0]
    assert ocr.calls == 1
    assert parsed.metadata.document_extraction.kind == "image"
    assert parsed.metadata.document_extraction.ocr_pages == [1]
    assert block.method == "ocr" and block.page == 1
    assert block.source_artifact == f"sha256:{sha256_file(source)}"
    assert block.region_space == "page_display_normalized" and block.region == (0.1, 0.2, 0.8, 0.4)
    assert block.confidence == 0.3
    assert any("低置信" in warning for warning in parsed.metadata.warnings)


def test_scanned_pdf_without_opt_in_does_not_claim_complete_text(tmp_path):
    source = tmp_path / "scan.pdf"
    _pdf(source, ["image"])
    with pytest.raises(Exception, match="扫描件需显式启用本地 OCR"):
        parse_pdf(source, _metadata(source, "pdf"))


def test_mixed_page_preserves_native_text_and_ocr_image_once(tmp_path):
    source = tmp_path / "mixed.pdf"
    _pdf(source, ["mixed"])
    ocr = FakeOCR()
    parsed = parse_pdf(source, _metadata(source, "pdf"), ocr)
    assert ocr.calls == 1
    assert {block.method for block in parsed.chapters[0].source_blocks} == {"native", "ocr"}
    assert "Native heading" in parsed.chapters[0].text
    assert "扫描页的中文观点" in parsed.chapters[0].text
    assert parsed.metadata.document_extraction.kind == "mixed"


def test_many_mixed_images_only_hit_region_limit_when_ocr_is_enabled(tmp_path):
    source = tmp_path / "many-images.pdf"
    document = pymupdf.open()
    page = document.new_page(width=500, height=330)
    page.insert_text((10, 16), "Native heading about reading", fontsize=10)
    for row in range(3):
        for column in range(3):
            x0, y0 = column * 165, 25 + row * 100
            page.insert_image(pymupdf.Rect(x0, y0, x0 + 160, y0 + 90), stream=_picture())
    document.save(source)
    document.close()
    native = parse_pdf(source, _metadata(source, "pdf"))
    assert native.metadata.coverage == "partial"
    with pytest.raises(Exception, match="图像区域过多"):
        parse_pdf(source, _metadata(source, "pdf"), FakeOCR())


def test_rotated_scanned_page_keeps_valid_region(tmp_path):
    source = tmp_path / "rotated.pdf"
    _pdf(source, ["image"], rotation=90)
    parsed = parse_pdf(source, _metadata(source, "pdf"), FakeOCR())
    block = parsed.chapters[0].source_blocks[0]
    assert block.page == 1
    assert all(0 <= coordinate <= 1 for coordinate in block.region)


def test_image_epub_uses_spine_page_and_image_locator(tmp_path):
    source = tmp_path / "picture.epub"
    book = epub.EpubBook()
    book.set_identifier("ocr-test")
    book.set_title("图片书")
    book.set_language("zh")
    page = epub.EpubHtml(title="图片页", file_name="pages/one.xhtml", lang="zh")
    page.set_content('<html><body><h1>图片页</h1><img src="../images/scan.png"/></body></html>')
    picture = epub.EpubItem(uid="scan", file_name="images/scan.png", media_type="image/png", content=_picture())
    book.add_item(page)
    book.add_item(picture)
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.spine = [page]
    epub.write_epub(str(source), book)
    ocr = FakeOCR()
    parsed = parse_epub(source, _metadata(source, "epub"), ocr)
    assert ocr.calls == 1
    image_block = next(block for block in parsed.chapters[0].source_blocks if block.method == "ocr")
    assert image_block.page == 1 and image_block.region_space == "image_normalized"
    assert image_block.resource_locator == "epub:images/scan.png"
    assert image_block.source_artifact == f"sha256:{sha256_file(source)}"
    assert parsed.metadata.document_extraction.kind == "mixed"


def test_image_only_epub_is_classified_as_image(tmp_path):
    source = tmp_path / "image only.epub"
    book = epub.EpubBook()
    book.set_identifier("image-only")
    book.set_title("图片书")
    book.set_language("zh")
    page = epub.EpubHtml(title="图片页", file_name="one.xhtml", lang="zh")
    page.set_content('<html><body><img src="scan.png"/></body></html>')
    book.add_item(page)
    book.add_item(epub.EpubItem(uid="scan", file_name="scan.png", media_type="image/png", content=_picture()))
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.spine = [page]
    epub.write_epub(str(source), book)
    parsed = parse_epub(source, _metadata(source, "epub"), FakeOCR())
    assert parsed.metadata.document_extraction.kind == "image"
    assert parsed.chapters[0].source_blocks[0].method == "ocr"


def test_isolated_inspection_rejects_damaged_pdf_without_raw_parser_error(tmp_path):
    source = tmp_path / "broken.pdf"
    source.write_bytes(b"%PDF-1.7\nnot a PDF body")
    result = CliRunner().invoke(app, ["inspect-document", str(source)])
    assert result.exit_code == 1
    assert "隔离 PDF 检测失败" in result.stderr
    assert "Traceback" not in result.stderr


def test_ocr_is_explicit_for_public_pipeline_api(tmp_path):
    source = tmp_path / "text.txt"
    source.write_text("第一章\n测试文本", encoding="utf-8")
    pipeline = Pipeline(MockLLMProvider(), MockTTSProvider(), tmp_path / "output")
    with pytest.raises(Exception, match="OCR 仅适用于 PDF 或 EPUB"):
        pipeline.generate(source, extraction_options=DocumentExtractionOptions(mode="auto", provider="apple-vision"))
    assert not (tmp_path / "output").exists()


def test_completed_ocr_parse_resume_does_not_call_provider_again(tmp_path):
    source = tmp_path / "scan.pdf"
    _pdf(source, ["image"])
    provider = FakeOCR()
    pipeline = Pipeline(MockLLMProvider(), MockTTSProvider(), tmp_path / "output")
    options = DocumentExtractionOptions(mode="auto", provider="apple-vision")
    calls = []

    def extract(path, metadata, name):
        calls.append(name)
        return parse_pdf(path, metadata, provider)

    with patch("bookcast.document_extraction.parse_with_ocr_isolated", side_effect=extract):
        root = pipeline.generate(source, extraction_options=options, mode="summary", minutes=1)
        assert provider.calls == 1
        before = (root / "chapters/0001.json").stat().st_mtime_ns
        pipeline.resume_job(root)
    assert provider.calls == 1
    assert calls == ["apple-vision"]
    assert (root / "chapters/0001.json").stat().st_mtime_ns == before
    assert load_manifest(root / "manifest.json").extraction_options == options


@pytest.mark.skipif(sys.platform != "darwin" or os.getenv("BOOKCAST_RUN_OCR_LIVE") != "1",
                    reason="Apple Vision 真实验收仅在 macOS 显式启用")
def test_apple_vision_real_chinese_english_and_rotated_page(tmp_path):
    from bookcast.adapters.apple_vision_ocr import AppleVisionOCRProvider
    from bookcast.document_extraction import parse_with_ocr_isolated

    provider = AppleVisionOCRProvider()
    source = tmp_path / "real-scan.pdf"
    _pdf(source, ["image"])
    parsed = parse_pdf(source, _metadata(source, "pdf"), provider)
    result = parsed.chapters[0].text
    assert "中文测试" in result and "ABC" in result
    rotated = tmp_path / "real-rotated.pdf"
    _pdf(rotated, ["image"], rotation=90)
    rotated_result = parse_pdf(rotated, _metadata(rotated, "pdf"), provider)
    assert "中文测试" in rotated_result.chapters[0].text
    isolated = parse_with_ocr_isolated(source, _metadata(source, "pdf"), "apple-vision")
    assert "中文测试" in isolated.chapters[0].text

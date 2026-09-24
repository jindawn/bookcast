"""Bounded PDF page detection and optional local OCR extraction."""

from dataclasses import dataclass
from pathlib import Path
import math

import pymupdf

from .document_extraction import OCRProvider
from .errors import BookCastError
from .models import BookMetadata, Chapter, DocumentExtractionInfo, NormalizedBook, SourceTextBlock
from .source_validation import validate_source


MAX_PDF_BYTES = 100 * 1024 * 1024
MAX_PAGES = 2500
MAX_TEXT_CHARACTERS = 20_000_000
MAX_RENDER_PIXELS = 16_000_000
MAX_OCR_REGIONS_PER_PAGE = 8
LOW_CONFIDENCE = 0.45


def _area(rect: pymupdf.Rect) -> float:
    return max(rect.width, 0) * max(rect.height, 0)


@dataclass(frozen=True)
class PDFPageInspection:
    page: int
    kind: str
    native_characters: int
    image_regions: int


def _page_parts(page) -> tuple[list[tuple[pymupdf.Rect, str]], list[pymupdf.Rect]]:
    native = [(pymupdf.Rect(block[:4]), block[4].strip())
              for block in page.get_text("blocks", sort=True)
              if block[6] == 0 and block[4].strip()]
    images = []
    for image in page.get_image_info():
        rect = pymupdf.Rect(image["bbox"]) & page.cropbox
        if not rect.is_empty and _area(rect) > 0:
            images.append(rect)
    return native, images


def _kind(native, images) -> str:
    if native and images:
        return "mixed"
    if native:
        return "text"
    if images:
        return "image"
    return "blank"


def _document_kind(pages: list[PDFPageInspection]) -> str:
    kinds = {page.kind for page in pages if page.kind != "blank"}
    return next(iter(kinds)) if len(kinds) == 1 else "mixed" if kinds else "blank"


def _open_pdf(path: Path):
    validate_source(path, "pdf", MAX_PDF_BYTES)
    try:
        document = pymupdf.open(path)
    except Exception as exc:
        raise BookCastError("PDF 容器无法安全打开。") from exc
    if document.needs_pass:
        document.close()
        raise BookCastError("PDF 需要密码，不支持加密 PDF。")
    if document.page_count > MAX_PAGES:
        document.close()
        raise BookCastError(f"PDF 超过 {MAX_PAGES} 页解析上限。")
    return document


def inspect_pdf(path: Path) -> dict:
    """Read-only classification; never invokes an OCR provider."""
    pages = []
    with _open_pdf(path) as document:
        for page in document:
            native, images = _page_parts(page)
            pages.append(PDFPageInspection(page.number + 1, _kind(native, images),
                                           sum(len(text) for _, text in native), len(images)))
    return {"kind": _document_kind(pages), "page_count": len(pages),
            "pages": [page.__dict__ for page in pages]}


def _region(rect: pymupdf.Rect, page) -> tuple[float, float, float, float]:
    display = rect * page.rotation_matrix
    display &= page.rect
    bounds = page.rect
    return (max(0.0, min(1.0, (display.x0 - bounds.x0) / bounds.width)),
            max(0.0, min(1.0, (display.y0 - bounds.y0) / bounds.height)),
            max(0.0, min(1.0, (display.x1 - bounds.x0) / bounds.width)),
            max(0.0, min(1.0, (display.y1 - bounds.y0) / bounds.height)))


def _targets(page, native, images, *, enforce_limit: bool) -> list[pymupdf.Rect]:
    if not native:
        return [page.rect] if images else []
    targets = []
    for image in images:
        if _area(image) / _area(page.cropbox) < 0.08:
            continue
        # A scanned image with an existing searchable text layer must not be OCRed twice.
        if any(len(text) >= 20 and _area(rect & image) / max(_area(rect), 1) > 0.5
               for rect, text in native):
            continue
        display = image * page.rotation_matrix
        if any(_area(display & old) / max(_area(display), 1) > 0.8 for old in targets):
            continue
        targets.append(display & page.rect)
    if enforce_limit and len(targets) > MAX_OCR_REGIONS_PER_PAGE:
        raise BookCastError("PDF 单页待 OCR 图像区域过多，请先清理文档。")
    return targets


def _ocr_region(page, target: pymupdf.Rect, metadata: BookMetadata, provider: OCRProvider) -> list[SourceTextBlock]:
    scale = 2.0
    if not all(math.isfinite(v) for v in (target.width, target.height)) or target.is_empty:
        raise BookCastError("PDF OCR 页面区域无效。")
    if target.width * target.height * scale * scale > MAX_RENDER_PIXELS:
        raise BookCastError("PDF OCR 页面像素超出安全上限。")
    pixmap = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), clip=target,
                             colorspace=pymupdf.csRGB, alpha=False, annots=False)
    if pixmap.width * pixmap.height > MAX_RENDER_PIXELS:
        raise BookCastError("PDF OCR 页面像素超出安全上限。")
    lines = provider.recognize(pixmap.tobytes("png"))
    blocks = []
    for line in lines:
        x0, y0, x1, y1 = line.region
        bounds = page.rect
        region = ((target.x0 - bounds.x0 + x0 * target.width) / bounds.width,
                  (target.y0 - bounds.y0 + y0 * target.height) / bounds.height,
                  (target.x0 - bounds.x0 + x1 * target.width) / bounds.width,
                  (target.y0 - bounds.y0 + y1 * target.height) / bounds.height)
        blocks.append(SourceTextBlock(text=line.text, method="ocr", page=page.number + 1,
                                      source_artifact=f"sha256:{metadata.source_sha256}",
                                      resource_locator=f"page:{page.number + 1}",
                                      region=region, region_space="page_display_normalized",
                                      confidence=line.confidence))
    return blocks


def extract_pdf(path: Path, metadata: BookMetadata, ocr: OCRProvider | None = None) -> NormalizedBook:
    chapters: list[Chapter] = []
    reports: list[PDFPageInspection] = []
    ocr_pages: list[int] = []
    total_characters = 0
    with _open_pdf(path) as document:
        metadata.title = document.metadata.get("title") or metadata.title
        author = document.metadata.get("author")
        metadata.authors = [author] if author else []
        for page in document:
            native, images = _page_parts(page)
            kind = _kind(native, images)
            number = page.number + 1
            reports.append(PDFPageInspection(number, kind, sum(len(text) for _, text in native), len(images)))
            blocks = [SourceTextBlock(text=text, method="native", page=number,
                                      source_artifact=f"sha256:{metadata.source_sha256}",
                                      resource_locator=f"page:{number}",
                                      region=_region(rect, page), region_space="page_display_normalized")
                      for rect, text in native]
            targets = _targets(page, native, images, enforce_limit=ocr is not None)
            if targets and ocr is None:
                metadata.warnings.append(f"PDF 第 {number} 页有未 OCR 的图像文字候选区域。")
                metadata.coverage = "partial"
            if ocr and targets:
                ocr_pages.append(number)
                for target in targets:
                    blocks.extend(_ocr_region(page, target, metadata, ocr))
                low = sum(block.method == "ocr" and block.confidence < LOW_CONFIDENCE for block in blocks)
                if low:
                    metadata.warnings.append(f"PDF 第 {number} 页有 {low} 个低置信 OCR 文本块，需核对原页。")
                    metadata.coverage = "partial"
                if not any(block.method == "ocr" for block in blocks):
                    metadata.warnings.append(f"PDF 第 {number} 页 OCR 未识别出文字。")
                    metadata.coverage = "partial"
            if not blocks:
                metadata.warnings.append(f"PDF 第 {number} 页无可用文字。")
                metadata.coverage = "partial"
                continue
            blocks.sort(key=lambda block: ((block.region or (0, 0, 0, 0))[1],
                                           (block.region or (0, 0, 0, 0))[0]))
            text = "\n".join(block.text for block in blocks)
            total_characters += len(text)
            if total_characters > MAX_TEXT_CHARACTERS:
                raise BookCastError("PDF 提取文本超过安全上限。")
            chapters.append(Chapter(id=f"{len(chapters) + 1:04d}", title=f"第 {number} 页", text=text,
                                    source_locator=f"page:{number}", source_blocks=blocks))
    if not chapters:
        raise BookCastError("PDF 没有可用正文；扫描件需显式启用本地 OCR。")
    metadata.document_extraction = DocumentExtractionInfo(kind=_document_kind(reports),
                                                           provider=ocr.name if ocr_pages and ocr else None,
                                                           ocr_pages=ocr_pages)
    metadata.chapter_ids = [chapter.id for chapter in chapters]
    return NormalizedBook(metadata=metadata, chapters=chapters)

"""Local-only parsers preserving reading order and source locations."""

import codecs
from pathlib import Path
import posixpath
import re
from urllib.parse import unquote, urlsplit

from bs4 import BeautifulSoup
from ebooklib import ITEM_DOCUMENT, epub
import pymupdf

from .document_extraction import OCRProvider
from .errors import BookCastError
from .models import BookMetadata, Chapter, DocumentExtractionInfo, NormalizedBook, SourceTextBlock
from .pdf_extraction import LOW_CONFIDENCE, extract_pdf
from .source_sanitation import (advertisement_unit_rule, filter_epub_body, source_record,
                                structural_rule)


HEADING = re.compile(r"^(?:第[0-9零〇一二三四五六七八九十百千两]+[章回节卷部].*|chapter\s+\S+.*|#{1,3}\s+.+)$", re.I)


def _chapter(index: int, title: str, text: str, locator: str) -> Chapter:
    return Chapter(id=f"{index:04d}", title=title, text=text.strip(), source_locator=locator)


def parse_txt(path: Path, metadata: BookMetadata) -> NormalizedBook:
    raw = path.read_bytes()
    encoding = "utf-16" if raw.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)) else "utf-8-sig"
    try:
        text = raw.decode(encoding)
    except UnicodeError as exc:
        raise BookCastError("TXT 编码无效：请提供 UTF-8 或带 BOM 的 UTF-16 文件。") from exc
    if "\x00" in text:
        raise BookCastError("TXT 包含 NUL 字符，可能是二进制或不支持的编码。")
    lines = text.splitlines()
    starts = [i for i, line in enumerate(lines) if HEADING.fullmatch(line.strip())]
    if not starts or starts[0] != 0:
        starts.insert(0, 0)
    chapters = []
    for start, end in zip(starts, starts[1:] + [len(lines)]):
        body = "\n".join(lines[start:end]).strip()
        if not body:
            continue
        title = lines[start].strip() if HEADING.fullmatch(lines[start].strip()) else metadata.title
        chapters.append(_chapter(len(chapters) + 1, title, body, f"lines:{start + 1}-{end}"))
    if not chapters:
        raise BookCastError("TXT 没有可解析的正文。")
    return _book(metadata, chapters)


def parse_epub(path: Path, metadata: BookMetadata, ocr: OCRProvider | None = None) -> NormalizedBook:
    book = epub.read_epub(str(path), options={"ignore_ncx": True})
    titles = book.get_metadata("DC", "title")
    languages = book.get_metadata("DC", "language")
    metadata.title = str(titles[0][0]) if titles else metadata.title
    metadata.authors = [str(value) for value, _ in book.get_metadata("DC", "creator") if value]
    metadata.language = str(languages[0][0]) if languages else None
    ordered = []
    seen = set()
    for item_id, _linear in book.spine:
        item = book.get_item_with_id(item_id)
        if item is None:
            raise BookCastError(f"EPUB spine 引用了缺失内容：{item_id}")
        if item_id not in seen:
            ordered.append(item)
            seen.add(item_id)
    for item in book.get_items_of_type(ITEM_DOCUMENT):
        if item.get_id() not in seen:
            ordered.append(item)
            if not isinstance(item, epub.EpubNav):
                metadata.warnings.append(f"非 spine 文档追加到末尾：{item.get_name()}")
    chapters = []
    resources = {item.get_name(): item for item in book.get_items()}
    image_items = 0
    ocr_pages = []
    source_filter = []
    for position, item in enumerate(ordered, 1):
        unit = f"epub:{item.get_name()}"
        if item.get_type() != ITEM_DOCUMENT:
            metadata.warnings.append(f"未解析的 spine 资源：{item.get_name()}")
            metadata.coverage = "partial"
            source_filter.append(source_record(unit, 'auxiliary', True, 'non_document_resource',
                                               'epub_resource_type', item.get_name()))
            continue
        soup = BeautifulSoup(item.get_body_content(), "html.parser")
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()
        original_text = soup.get_text("\n", strip=True)
        structure = structural_rule(item.get_name(), list(getattr(item, 'properties', [])), soup)
        if isinstance(item, epub.EpubNav):
            structure = ('table_of_contents', 'epub_nav_document')
        if structure:
            classification, rule = structure
            source_filter.append(source_record(unit, classification, True, rule, rule, original_text))
            continue
        promotion = advertisement_unit_rule(soup, original_text)
        if promotion:
            source_filter.append(source_record(unit, 'advertisement', True, promotion, promotion, original_text))
            continue
        source_filter.extend(filter_epub_body(soup, unit))
        heading = soup.find(["h1", "h2", "h3"])
        title = heading.get_text(" ", strip=True) if heading else item.get_name()
        text = soup.get_text("\n", strip=True)
        if text:
            source_filter.append(source_record(unit, 'body', False,
                                               'spine_content' if item.get_id() in seen else 'non_spine_content',
                                               'retained_by_default', text))
        blocks = ([SourceTextBlock(text=text, method="native", page=position,
                                   source_artifact=f"sha256:{metadata.source_sha256}",
                                   resource_locator=f"epub:{item.get_name()}")]
                  if text else [])
        images = soup.find_all("img")
        image_items += len(images)
        if ocr:
            for image in images:
                raw_src = str(image.get("src") or "")
                parsed_src = urlsplit(raw_src)
                if parsed_src.scheme or parsed_src.netloc or not parsed_src.path or parsed_src.path.startswith("/"):
                    metadata.warnings.append(f"EPUB 跳过外部或无效图片引用：{item.get_name()}")
                    metadata.coverage = "partial"
                    continue
                name = posixpath.normpath(posixpath.join(posixpath.dirname(item.get_name()),
                                                         unquote(parsed_src.path)))
                resource = resources.get(name) if not name.startswith("../") else None
                if resource is None or resource.media_type not in {"image/png", "image/jpeg"}:
                    metadata.warnings.append(f"EPUB 图片不可 OCR：{name}")
                    metadata.coverage = "partial"
                    continue
                data = resource.get_content()
                if len(data) > 16 * 1024 * 1024:
                    raise BookCastError("EPUB OCR 图片超过 16 MiB 上限。")
                pixmap = pymupdf.Pixmap(data)
                if pixmap.width * pixmap.height > 16_000_000:
                    raise BookCastError("EPUB OCR 图片像素超出安全上限。")
                if pixmap.colorspace != pymupdf.csRGB or pixmap.alpha:
                    pixmap = pymupdf.Pixmap(pymupdf.csRGB, pixmap)
                recognized = ocr.recognize(pixmap.tobytes("png"))
                if position not in ocr_pages:
                    ocr_pages.append(position)
                for line in recognized:
                    blocks.append(SourceTextBlock(text=line.text, method="ocr", page=position,
                                                   source_artifact=f"sha256:{metadata.source_sha256}",
                                                   resource_locator=f"epub:{name}", region=line.region,
                                                   region_space="image_normalized", confidence=line.confidence))
                if not recognized:
                    metadata.warnings.append(f"EPUB 图片 OCR 未识别出文字：{name}")
                    metadata.coverage = "partial"
                elif any(line.confidence < LOW_CONFIDENCE for line in recognized):
                    metadata.warnings.append(f"EPUB 图片有低置信 OCR 文字，需核对原图：{name}")
                    metadata.coverage = "partial"
        if not blocks:
            metadata.warnings.append(f"无可提取文本，可能是图片页：{item.get_name()}")
            metadata.coverage = "partial"
            source_filter.append(source_record(unit, 'auxiliary', True, 'empty_after_extraction',
                                               'no_text_or_ocr', original_text))
            continue
        if images and not ocr:
            metadata.warnings.append(f"图片内容未 OCR：{item.get_name()}")
            metadata.coverage = "partial"
        chapters.append(Chapter(id=f"{len(chapters) + 1:04d}", title=title,
                                text="\n".join(block.text for block in blocks),
                                source_locator=f"epub:{item.get_name()}", source_blocks=blocks))
    if not chapters:
        raise BookCastError("EPUB 没有可解析正文；图片书需显式启用 OCR，DRM 文件不受支持。")
    has_native = any(block.method == "native" for chapter in chapters for block in chapter.source_blocks)
    kind = "mixed" if image_items and has_native else "image" if image_items else "text"
    metadata.document_extraction = DocumentExtractionInfo(
        kind=kind, provider=ocr.name if ocr_pages and ocr else None, ocr_pages=ocr_pages)
    return _book(metadata, chapters, source_filter)


def parse_pdf(path: Path, metadata: BookMetadata, ocr: OCRProvider | None = None) -> NormalizedBook:
    return extract_pdf(path, metadata, ocr)


def _book(metadata: BookMetadata, chapters: list[Chapter], source_filter=None) -> NormalizedBook:
    metadata.chapter_ids = [chapter.id for chapter in chapters]
    return NormalizedBook(metadata=metadata, chapters=chapters, source_filter=source_filter or [])


def parse_book(path: Path, metadata: BookMetadata, ocr: OCRProvider | None = None) -> NormalizedBook:
    try:
        if metadata.source_format == "txt":
            return parse_txt(path, metadata)
        if metadata.source_format == "epub":
            return parse_epub(path, metadata, ocr)
        return parse_pdf(path, metadata, ocr)
    except BookCastError:
        raise
    except Exception as exc:
        raise BookCastError(f"无法解析 {metadata.source_format.upper()}：{exc}") from exc

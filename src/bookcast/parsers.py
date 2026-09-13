"""Local-only parsers preserving reading order and source locations."""

import codecs
from pathlib import Path
import re

from bs4 import BeautifulSoup
from ebooklib import ITEM_DOCUMENT, epub
import pymupdf

from .errors import BookCastError
from .models import BookMetadata, Chapter, NormalizedBook


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


def parse_epub(path: Path, metadata: BookMetadata) -> NormalizedBook:
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
        if item.get_id() not in seen and not isinstance(item, epub.EpubNav):
            ordered.append(item)
            metadata.warnings.append(f"非 spine 文档追加到末尾：{item.get_name()}")
    chapters = []
    for item in ordered:
        if isinstance(item, epub.EpubNav) or "nav" in getattr(item, "properties", []):
            continue
        if item.get_type() != ITEM_DOCUMENT:
            metadata.warnings.append(f"未解析的 spine 资源：{item.get_name()}")
            metadata.coverage = "partial"
            continue
        soup = BeautifulSoup(item.get_body_content(), "html.parser")
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()
        heading = soup.find(["h1", "h2", "h3"])
        title = heading.get_text(" ", strip=True) if heading else item.get_name()
        text = soup.get_text("\n", strip=True)
        if not text:
            metadata.warnings.append(f"无可提取文本，可能是图片页：{item.get_name()}")
            metadata.coverage = "partial"
            continue
        if soup.find("img"):
            metadata.warnings.append(f"图片内容未 OCR：{item.get_name()}")
            metadata.coverage = "partial"
        chapters.append(_chapter(len(chapters) + 1, title, text, f"epub:{item.get_name()}"))
    if not chapters:
        raise BookCastError("EPUB 没有可解析正文；图片书和 DRM 加密书不受支持。")
    return _book(metadata, chapters)


def parse_pdf(path: Path, metadata: BookMetadata) -> NormalizedBook:
    chapters = []
    with pymupdf.open(path) as document:
        if document.needs_pass:
            raise BookCastError("PDF 需要密码，本阶段不支持加密 PDF。")
        metadata.title = document.metadata.get("title") or metadata.title
        author = document.metadata.get("author")
        metadata.authors = [author] if author else []
        for page in document:
            text = page.get_text("text", sort=True).strip()
            if not text:
                metadata.warnings.append(f"PDF 第 {page.number + 1} 页没有文本，可能需要 OCR。")
                metadata.coverage = "partial"
                continue
            chapters.append(_chapter(len(chapters) + 1, f"第 {page.number + 1} 页", text, f"page:{page.number + 1}"))
    if not chapters:
        raise BookCastError("PDF 没有可提取文本，可能是扫描件；本阶段未实现 OCR。")
    return _book(metadata, chapters)


def _book(metadata: BookMetadata, chapters: list[Chapter]) -> NormalizedBook:
    metadata.chapter_ids = [chapter.id for chapter in chapters]
    return NormalizedBook(metadata=metadata, chapters=chapters)


def parse_book(path: Path, metadata: BookMetadata) -> NormalizedBook:
    parsers = {"txt": parse_txt, "epub": parse_epub, "pdf": parse_pdf}
    try:
        return parsers[metadata.source_format](path, metadata)
    except BookCastError:
        raise
    except Exception as exc:
        raise BookCastError(f"无法解析 {metadata.source_format.upper()}：{exc}") from exc

"""Validate downloaded containers without executing or extracting their contents."""

import codecs
from pathlib import Path, PurePosixPath
import re
import stat
import zipfile

from .errors import BookCastError

MIMES = {"txt": {"text/plain"}, "epub": {"application/epub+zip"}, "pdf": {"application/pdf"}}


def validate_source(path: Path, fmt: str, max_bytes: int, expected_book_id: int | None = None) -> None:
    if not path.is_file() or path.stat().st_size == 0 or path.stat().st_size > max_bytes:
        raise BookCastError("源文件为空、不存在或超过大小上限。")
    try:
        with path.open("rb") as stream:
            prefix = stream.read(16 * 1024)
        if fmt == "txt":
            raw = path.read_bytes()
            encoding = "utf-16" if raw.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)) else "utf-8-sig"
            text = raw.decode(encoding)
            if (not text.strip() or any(ord(c) < 32 and c not in "\t\r\n\f" for c in text)
                    or re.search(r"<!doctype\s+html|<html\b|<script\b", text[:2048], re.I)):
                raise BookCastError("TXT 是空白、二进制或伪装的 HTML 内容。")
            if expected_book_id is not None and not re.search(
                    rf"\[\s*eBook\s*#\s*{expected_book_id}\s*\]", text[:16384], re.I):
                raise BookCastError("下载文本的 Gutenberg 编号与所选版本不符。")
        elif fmt == "pdf":
            if not prefix.startswith(b"%PDF-"):
                raise BookCastError("PDF 文件签名无效。")
        elif fmt == "epub":
            with zipfile.ZipFile(path) as archive:
                entries = archive.infolist()
                if len(entries) > 5000 or sum(e.file_size for e in entries) > 64 * 1024 * 1024:
                    raise BookCastError("EPUB 解压大小或条目数量超过上限。")
                names = set()
                for entry in entries:
                    name = entry.filename
                    parts = PurePosixPath(name)
                    if (name in names or parts.is_absolute() or ".." in parts.parts or "\\" in name
                            or ":" in name or stat.S_ISLNK(entry.external_attr >> 16)
                            or entry.flag_bits & 1 or entry.file_size > max(1, entry.compress_size) * 200):
                        raise BookCastError("EPUB 包含不安全路径、加密条目或异常压缩数据。")
                    names.add(name)
                    # EPUB's manifest may label an arbitrary extension as XML.
                    # Scan every bounded entry, not just conventional filenames.
                    if not entry.is_dir():
                        data = archive.read(entry).replace(b"\x00", b"")
                        # HTML5's inert declaration is safe; external/internal DTDs are not.
                        data = re.sub(br"<!DOCTYPE\s+html\s*>", b"", data, flags=re.I)
                        if re.search(br"<!DOCTYPE|<!ENTITY", data, re.I):
                            raise BookCastError("EPUB 不接受 DTD 或实体声明。")
                if archive.read("mimetype").strip() != b"application/epub+zip" or "META-INF/container.xml" not in names:
                    raise BookCastError("EPUB 容器标识无效。")
        else:
            raise BookCastError("只接受 EPUB/PDF/TXT。")
    except (UnicodeError, zipfile.BadZipFile, KeyError, RuntimeError):
        raise BookCastError("源文件编码或容器结构无效。") from None

"""Local document text extraction boundary, separate from AI and speech providers."""

import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .errors import BookCastError


class OCRText(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=4096)
    # Normalized image coordinates, top-left origin.
    region: tuple[float, float, float, float]
    confidence: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def valid_region(self):
        x0, y0, x1, y1 = self.region
        if not (0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1):
            raise ValueError("OCR 文本区域坐标无效。")
        return self


class OCRProvider(Protocol):
    name: str
    cache_key: str

    def recognize(self, png: bytes) -> list[OCRText]: ...


def create_ocr_provider(name: str) -> OCRProvider:
    if name == "apple-vision":
        from .adapters.apple_vision_ocr import AppleVisionOCRProvider

        return AppleVisionOCRProvider()
    raise BookCastError(f"未知 OCR Provider：{name}")


def ocr_cache_key(name: str) -> str:
    if name == "apple-vision":
        from .adapters.apple_vision_ocr import AppleVisionOCRProvider

        return AppleVisionOCRProvider.cache_key
    raise BookCastError(f"未知 OCR Provider：{name}")


def parse_with_ocr_isolated(path: Path, metadata, provider_name: str):
    """Open/render untrusted OCR documents in a bounded child process."""
    from .models import NormalizedBook

    payload = json.dumps({"path": str(path), "metadata": metadata.model_dump(mode="json"),
                          "provider": provider_name}, ensure_ascii=False).encode("utf-8")
    environment = _worker_environment()
    try:
        result = subprocess.run([sys.executable, "-m", "bookcast.document_extraction", "worker"],
                                input=payload, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                env=environment, timeout=3600, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise BookCastError("隔离文档提取进程无法启动或超时。") from exc
    if result.returncode or len(result.stdout) > 64 * 1024 * 1024:
        raise BookCastError("隔离文档提取失败；源文件可能损坏或超出资源上限。")
    try:
        return NormalizedBook.model_validate_json(result.stdout)
    except ValueError as exc:
        raise BookCastError("隔离文档提取返回无效结果。") from exc


def inspect_pdf_isolated(path: Path) -> dict:
    """Classify an untrusted PDF outside the CLI process without invoking OCR."""
    try:
        result = subprocess.run([sys.executable, "-m", "bookcast.document_extraction", "inspect"],
                                input=json.dumps({"path": str(path)}).encode("utf-8"),
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                env=_worker_environment(), timeout=120, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise BookCastError("隔离 PDF 检测进程无法启动或超时。") from exc
    if result.returncode or len(result.stdout) > 2 * 1024 * 1024:
        raise BookCastError("隔离 PDF 检测失败；源文件可能损坏或超出资源上限。")
    try:
        return json.loads(result.stdout)
    except ValueError as exc:
        raise BookCastError("隔离 PDF 检测返回无效结果。") from exc


def _worker_environment() -> dict[str, str]:
    # No LLM/TTS credentials or user-configured secrets are needed by local OCR.
    return {key: os.environ[key] for key in ("PATH", "HOME", "TMPDIR", "LANG", "VIRTUAL_ENV", "PYTHONPATH")
            if key in os.environ}


def _worker() -> None:
    # The worker never opens links or invokes embedded PDF actions. It only reads
    # the validated local file, renders bounded pages, and calls local OCR.
    from .models import BookMetadata
    from .parsers import parse_book

    try:
        payload = json.loads(sys.stdin.buffer.read(1024 * 1024))
        source = Path(payload["path"])
        if sys.argv[1] == "inspect":
            from .pdf_extraction import inspect_pdf

            sys.stdout.buffer.write(json.dumps(inspect_pdf(source), ensure_ascii=False).encode("utf-8"))
        else:
            metadata = BookMetadata.model_validate(payload["metadata"])
            provider = create_ocr_provider(payload["provider"])
            book = parse_book(source, metadata, provider)
            sys.stdout.buffer.write(book.model_dump_json().encode("utf-8"))
    except Exception:
        # Raw parser/OS errors must not be persisted in the parent Job.
        raise SystemExit(1) from None


if __name__ == "__main__" and sys.argv[1:] in (["worker"], ["inspect"]):
    _worker()

"""Resolve an explicit edition, import safely, and checkpoint normalized text."""

from pathlib import Path

from .errors import BookCastError
from .models import BookMetadata, utc_now
from .parsers import parse_book
from .source_api import AcquisitionRecord, EditionCandidate, SearchResult, SourceOffer
from .source_http import SafeHTTP, public_url
from .source_validation import MIMES, validate_source
from .storage import artifact_path, atomic_target, fingerprint, job_lock, sha256_file, write_json


def choose_edition(result: SearchResult, selected: str | None = None) -> EditionCandidate:
    if not result.candidates:
        raise BookCastError("没有匹配书籍；请细化书名，或提供 --file / --url。")
    if selected:
        matches = [candidate for candidate in result.candidates if candidate.id == selected]
        if len(matches) == 1:
            return matches[0]
        raise BookCastError("--edition 必须是本次候选列表中的唯一 ID。")
    if not result.complete or len(result.candidates) != 1:
        raise BookCastError("存在多个候选或结果不完整；请查看列表并用 --edition 明确选择。")
    return result.candidates[0]


def _valid(root: Path, artifacts: dict) -> bool:
    try:
        return bool(artifacts) and all(sha256_file(artifact_path(root, name)) == digest for name, digest in artifacts.items())
    except (OSError, BookCastError):
        return False


class Acquirer:
    def __init__(self, output_dir: Path = Path("imports"), http: SafeHTTP | None = None):
        self.output_dir, self.http = output_dir.resolve(), http or SafeHTTP()

    def acquire(self, candidate: EditionCandidate, offer: SourceOffer, *, max_bytes: int = 32 * 1024 * 1024) -> Path:
        if offer.candidate_id != candidate.id or offer.provider != candidate.provider:
            raise BookCastError("来源与所选版本不匹配。")
        if not offer.eligible or offer.rights_category == "unknown":
            raise BookCastError("没有已认可的书籍使用依据；用户 URL 需 --rights-confirmed，未知版权来源不下载。")
        if bool(offer.url) == bool(offer.local_path):
            raise BookCastError("来源必须且只能包含一个 URL 或本地文件。")
        if not 0 < max_bytes <= 100 * 1024 * 1024:
            raise BookCastError("文件大小上限必须在 1 字节到 100 MiB 之间。")
        local = Path(offer.local_path) if offer.local_path else None
        if offer.url:
            public_url(offer.url)
        if local:
            validate_source(local, offer.format, max_bytes)
        local_hash = sha256_file(local) if local else None
        key = fingerprint({"candidate": candidate.model_dump(), "format": offer.format, "url": offer.url,
                           "local_sha256": local_hash})[:24]
        root = artifact_path(self.output_dir, key)
        root.mkdir(parents=True, exist_ok=True)
        with job_lock(root):
            manifest_path = artifact_path(root, "acquisition.json")
            source_name = f"source/input.{offer.format}"
            source = artifact_path(root, source_name)
            record = AcquisitionRecord(id=key, candidate=candidate, source=offer).model_dump()
            if manifest_path.exists():
                try:
                    record = AcquisitionRecord.model_validate_json(manifest_path.read_text(encoding="utf-8")).model_dump()
                    if record["schema_version"] != 1 or record["id"] != key or record["candidate"] != candidate.model_dump():
                        raise ValueError()
                except (OSError, ValueError, KeyError, TypeError):
                    raise BookCastError("获取任务记录无效，拒绝覆盖；请使用新的 --output-dir。") from None
            elif any(p.name != ".lock" for p in root.iterdir()):
                raise BookCastError("获取目录非空且无记录，拒绝覆盖。")

            def save(status):
                record["status"], record["updated_at"] = status, utc_now()
                write_json(manifest_path, record)

            try:
                downloaded = record.get("download")
                if not downloaded or not _valid(root, {source_name: downloaded["sha256"]}):
                    save("downloading")
                    validator = lambda path: validate_source(path, offer.format, max_bytes, offer.expected_book_id)
                    if local:
                        with atomic_target(source) as temporary:
                            count = 0
                            with local.open("rb") as reader, temporary.open("wb") as writer:
                                while chunk := reader.read(64 * 1024):
                                    count += len(chunk)
                                    if count > max_bytes:
                                        raise BookCastError("本地文件复制过程中超过大小限制。")
                                    writer.write(chunk)
                            validator(temporary)
                            if sha256_file(temporary) != local_hash:
                                raise BookCastError("本地文件在导入过程中发生变化。")
                        downloaded = {"sha256": sha256_file(source), "size": source.stat().st_size,
                                      "mime": next(iter(MIMES[offer.format])), "url": None}
                    else:
                        downloaded = self.http.download(offer.url, source, max_bytes=max_bytes,
                                                        allowed_mimes=MIMES[offer.format], validate=validator)
                    record["download"] = downloaded
                    save("downloaded")
                validate_source(source, offer.format, max_bytes, offer.expected_book_id)
                # Recheck current source eligibility on each invocation; preserve completed artifacts.
                if record.get("status") == "parsed" and _valid(root, record.get("artifacts", {})):
                    return root
                record["source"], record["error"] = offer.model_dump(), None
                save("parsing")
                digest = downloaded["sha256"]
                book_id = fingerprint({"source_sha256": digest, "format": offer.format})[:24]
                metadata = BookMetadata(book_id=book_id, title=candidate.identity.title, authors=candidate.identity.authors,
                    language=candidate.identity.language, source_name=source.name, source_sha256=digest, source_format=offer.format,
                    acquisition={"identity": candidate.identity.model_dump(), "candidate_id": candidate.id,
                                 "metadata_origin": candidate.metadata_origin, "source": offer.model_dump(), "download": downloaded})
                book = parse_book(source, metadata)
                names = ["metadata.json"]
                write_json(artifact_path(root, "metadata.json"), book.metadata.model_dump())
                for chapter in book.chapters:
                    name = f"chapters/{chapter.id}.json"
                    write_json(artifact_path(root, name), chapter.model_dump())
                    names.append(name)
                record["artifacts"] = {name: sha256_file(artifact_path(root, name)) for name in names}
                save("parsed")
            except (Exception, KeyboardInterrupt, SystemExit) as exc:
                record["error"] = str(exc) if isinstance(exc, BookCastError) else "获取或解析中断；修复后重复相同命令。"
                save("failed")
                if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                    raise
                raise BookCastError(record["error"]) from None
        return root

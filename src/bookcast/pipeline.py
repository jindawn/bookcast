"""Chapter-granular checkpoints with input fingerprints and artifact hashes."""

from collections.abc import Callable
from pathlib import Path
import shutil

from pydantic import ValidationError

from .audio import merge_audio, validate_wav
from .errors import BookCastError
from .models import BookMetadata, Chapter, ChapterAnalysis, Manifest, PodcastScript, StepRecord, utc_now
from .parsers import parse_book
from .providers import LLMProvider, TTSProvider
from .storage import artifact_path, atomic_target, fingerprint, job_lock, sha256_file, write_json


def load_manifest(path: Path) -> Manifest:
    try:
        return Manifest.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise BookCastError(f"无法读取有效任务 manifest：{path}；{exc}") from exc


def artifacts_valid(root: Path, record: StepRecord) -> bool:
    if not record.artifacts:
        return False
    try:
        return all(artifact_path(root, name).is_file() and sha256_file(artifact_path(root, name)) == digest
                   for name, digest in record.artifacts.items())
    except OSError:
        return False


class Pipeline:
    def __init__(self, llm: LLMProvider, tts: TTSProvider, output_dir: Path = Path("output")):
        self.llm, self.tts = llm, tts
        self.output_dir = output_dir.resolve()

    def generate(self, source: Path, *, resume: bool = False) -> Path:
        source = source.resolve()
        if not source.is_file():
            raise BookCastError(f"输入文件不存在：{source}")
        source_format = source.suffix.lower().lstrip(".")
        if source_format not in {"epub", "pdf", "txt"}:
            raise BookCastError("仅支持本地 EPUB、PDF 和 TXT 文件。")
        digest = sha256_file(source)
        book_id = fingerprint({"source_sha256": digest, "format": source_format})[:24]
        root = artifact_path(self.output_dir, book_id)
        if resume and not (root / "manifest.json").is_file():
            raise BookCastError("没有匹配的可恢复任务。输入内容改变会生成新 book_id，请先不加 --resume 运行。")
        root.mkdir(parents=True, exist_ok=True)
        with job_lock(root):
            manifest_path = artifact_path(root, "manifest.json")
            config = {"llm": self.llm.cache_key, "tts": self.tts.cache_key}
            if manifest_path.exists():
                manifest = load_manifest(manifest_path)
                if (manifest.book_id != book_id or manifest.source_sha256 != digest
                        or manifest.source_format != source_format or manifest.config != config):
                    raise BookCastError("任务输入或 Provider 配置不匹配；请使用另一个 --output-dir，避免覆盖已有任务。")
                if not resume and manifest.status != "completed":
                    raise BookCastError(f"任务尚未完成，请使用 --resume 继续：{book_id}")
            else:
                if any(path.name != ".lock" for path in root.iterdir()):
                    raise BookCastError("目标任务目录非空且没有 manifest，拒绝覆盖已有数据。")
                manifest = Manifest(book_id=book_id, source_sha256=digest, source_name=source.name,
                                    source_format=source_format, config=config)
                write_json(manifest_path, manifest.model_dump())
            runner = _Runner(root, manifest, self.llm, self.tts)
            try:
                runner.run(source)
            except (Exception, KeyboardInterrupt) as exc:
                manifest.status = "failed"
                manifest.error = str(exc) or "任务已中断"
                runner.save()
                if isinstance(exc, KeyboardInterrupt):
                    raise
                raise BookCastError(f"任务 {book_id} 失败：{manifest.error}；修复原因后加 --resume 继续。") from exc
        return root


class _Runner:
    def __init__(self, root: Path, manifest: Manifest, llm: LLMProvider, tts: TTSProvider):
        self.root, self.manifest, self.llm, self.tts = root, manifest, llm, tts

    def path(self, relative: str) -> Path:
        return artifact_path(self.root, relative)

    def save(self) -> None:
        self.manifest.updated_at = utc_now()
        write_json(self.path("manifest.json"), self.manifest.model_dump())

    def step(self, name: str, inputs: object, operation: Callable[[], list[str]]) -> None:
        key = fingerprint({"pipeline": self.manifest.pipeline_version, "step": name, "inputs": inputs})
        previous = self.manifest.steps.get(name)
        if (previous and previous.status == "completed" and previous.fingerprint == key
                and artifacts_valid(self.root, previous)):
            return
        record = StepRecord(status="running", fingerprint=key, attempts=previous.attempts + 1 if previous else 1)
        self.manifest.steps[name] = record
        self.manifest.status, self.manifest.error = "running", None
        self.save()
        try:
            outputs = operation()
            record.artifacts = {name: sha256_file(self.path(name)) for name in outputs}
            if not record.artifacts:
                raise BookCastError(f"步骤 {name} 没有产生文件。")
            record.status = "completed"
        except (Exception, KeyboardInterrupt) as exc:
            record.status, record.error = "failed", str(exc) or "任务已中断"
            raise
        finally:
            record.updated_at = utc_now()
            self.save()

    def run(self, source: Path) -> None:
        manifest = self.manifest
        source_relative = f"source/input.{manifest.source_format}"

        def import_source() -> list[str]:
            with atomic_target(self.path(source_relative)) as temporary:
                shutil.copyfile(source, temporary)
                if sha256_file(temporary) != manifest.source_sha256:
                    raise BookCastError("输入文件在读取期间发生变化，请重新运行。")
            return [source_relative]

        self.step("input", manifest.source_sha256, import_source)

        def parse() -> list[str]:
            metadata = BookMetadata(book_id=manifest.book_id, title=Path(manifest.source_name).stem,
                                    source_name=manifest.source_name, source_sha256=manifest.source_sha256,
                                    source_format=manifest.source_format)
            book = parse_book(self.path(source_relative), metadata)
            outputs = []
            for chapter in book.chapters:
                name = f"chapters/{chapter.id}.json"
                write_json(self.path(name), chapter.model_dump())
                outputs.append(name)
            write_json(self.path("metadata.json"), book.metadata.model_dump())
            return ["metadata.json", *outputs]

        self.step("parse", {"source": sha256_file(self.path(source_relative)), "format": manifest.source_format}, parse)
        metadata = BookMetadata.model_validate_json(self.path("metadata.json").read_text(encoding="utf-8"))
        manifest.warnings = metadata.warnings
        for chapter_id in metadata.chapter_ids:
            chapter_name = f"chapters/{chapter_id}.json"
            chapter = Chapter.model_validate_json(self.path(chapter_name).read_text(encoding="utf-8"))
            analysis_name, script_name, audio_name = (f"analysis/{chapter_id}.json", f"scripts/{chapter_id}.json", f"audio/{chapter_id}.wav")

            def analyze() -> list[str]:
                analysis = ChapterAnalysis.model_validate(self.llm.analyze(chapter))
                if analysis.chapter_id != chapter.id or analysis.source_locator != chapter.source_locator:
                    raise BookCastError("LLM 分析返回了错误的章节或来源位置。")
                write_json(self.path(analysis_name), analysis.model_dump())
                return [analysis_name]

            self.step(f"analysis:{chapter_id}", {"chapter": sha256_file(self.path(chapter_name)), "llm": self.llm.cache_key}, analyze)
            analysis = ChapterAnalysis.model_validate_json(self.path(analysis_name).read_text(encoding="utf-8"))

            def script() -> list[str]:
                result = PodcastScript.model_validate(self.llm.script(chapter, analysis))
                if (result.chapter_id != chapter.id or result.source_locator != chapter.source_locator
                        or {turn.speaker for turn in result.turns} != {"主持人", "嘉宾"}):
                    raise BookCastError("播客脚本必须对应当前章节，且包含两位说话者。")
                write_json(self.path(script_name), result.model_dump())
                return [script_name]

            self.step(f"script:{chapter_id}", {"analysis": sha256_file(self.path(analysis_name)),
                      "chapter": sha256_file(self.path(chapter_name)), "llm": self.llm.cache_key}, script)
            podcast = PodcastScript.model_validate_json(self.path(script_name).read_text(encoding="utf-8"))

            def tts() -> list[str]:
                with atomic_target(self.path(audio_name)) as temporary:
                    self.tts.synthesize(podcast, temporary)
                    validate_wav(temporary)
                return [audio_name]

            self.step(f"tts:{chapter_id}", {"script": sha256_file(self.path(script_name)), "tts": self.tts.cache_key}, tts)

        def merge() -> list[str]:
            with atomic_target(self.path("podcast.mp3")) as temporary:
                merge_audio(self.root, metadata.chapter_ids, temporary)
            return ["podcast.mp3"]

        audio_hashes = {cid: sha256_file(self.path(f"audio/{cid}.wav")) for cid in metadata.chapter_ids}
        self.step("merge", {"ordered_audio": list(audio_hashes.items()), "codec": "libmp3lame:96k"}, merge)

        def output() -> list[str]:
            write_json(self.path("audio/export.json"), {
                "book_id": manifest.book_id, "file": "podcast.mp3", "format": "mp3",
                "sha256": sha256_file(self.path("podcast.mp3")), "chapters": metadata.chapter_ids,
                "providers": manifest.config, "coverage": metadata.coverage, "warnings": metadata.warnings,
                "note": "默认 Mock Provider 输出测试音调，不是真实人声播客。",
            })
            return ["audio/export.json"]

        self.step("output", {"mp3": sha256_file(self.path("podcast.mp3")), "metadata": sha256_file(self.path("metadata.json"))}, output)
        if manifest.status != "completed":
            manifest.status, manifest.error = "completed", None
            self.save()


def job_status(job: str, output_dir: Path = Path("output")) -> dict:
    candidate = Path(job)
    if candidate.is_dir():
        path = candidate / "manifest.json"
    elif candidate.is_file():
        path = candidate
    else:
        path = artifact_path(output_dir.resolve(), job) / "manifest.json"
    manifest = load_manifest(path)
    damaged = [name for name, record in manifest.steps.items()
               if record.status == "completed" and not artifacts_valid(path.parent.resolve(), record)]
    return {**manifest.model_dump(), "integrity": "damaged" if damaged else "ok", "damaged_steps": damaged}

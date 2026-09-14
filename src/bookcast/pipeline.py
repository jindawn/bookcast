"""Chapter-granular checkpoints with input fingerprints and artifact hashes."""

from collections.abc import Callable
from pathlib import Path
import shutil

from .audio import merge_audio, validate_wav
from .errors import BookCastError
from .content_models import ContentOptions
from .models import AIAttempt, BookMetadata, Chapter, ChapterAnalysis, Manifest, PodcastScript, StepRecord, utc_now
from .parsers import parse_book
from .provider_api import LLMProvider, TTSProvider, ProviderError, ProviderStatus, ErrorKind
from .provider_chain import ProviderChain
from .prompts import ANALYSIS_VERSION, SCRIPT_VERSION, TTS_VERSION, analysis_prompt, script_prompt
from .storage import artifact_path, atomic_target, fingerprint, job_lock, sha256_file, write_json


def load_manifest(path: Path) -> Manifest:
    try:
        return Manifest.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise BookCastError(f"无法读取有效任务 manifest：{path}") from None


def artifacts_valid(root: Path, record: StepRecord | AIAttempt) -> bool:
    if not record.artifacts:
        return False
    try:
        return all(artifact_path(root, name).is_file() and sha256_file(artifact_path(root, name)) == digest
                   for name, digest in record.artifacts.items())
    except OSError:
        return False


class Pipeline:
    def __init__(self, llm: LLMProvider | ProviderChain, tts: TTSProvider | ProviderChain, output_dir: Path = Path("output")):
        self.llm = llm if isinstance(llm, ProviderChain) else ProviderChain([llm])
        self.tts = tts if isinstance(tts, ProviderChain) else ProviderChain([tts])
        self.output_dir = output_dir.resolve()

    def generate(self, source: Path, *, resume: bool = False, metadata_seed: BookMetadata | None = None,
                 mode: str | None = None, minutes: int | None = None, revise_segment: str | None = None) -> Path:
        if revise_segment and not resume:
            raise BookCastError("修订片段需要 --resume。")
        options = ContentOptions(mode="two_host" if mode is None else mode, minutes=10 if minutes is None else minutes)
        source = source.resolve()
        if not source.is_file():
            raise BookCastError(f"输入文件不存在：{source}")
        source_format = source.suffix.lower().lstrip(".")
        if source_format not in {"epub", "pdf", "txt"}:
            raise BookCastError("仅支持本地 EPUB、PDF 和 TXT 文件。")
        digest = sha256_file(source)
        if metadata_seed and (metadata_seed.source_sha256 != digest or metadata_seed.source_format != source_format):
            raise BookCastError("获取元数据与源文件不一致。")
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
                        or manifest.source_format != source_format):
                    raise BookCastError("任务输入不匹配，请使用另一个 --output-dir。")
                if manifest.pipeline_version == "2":
                    stored = ContentOptions.model_validate(manifest.content_options)
                    requested = ContentOptions(mode=stored.mode if mode is None else mode, minutes=stored.minutes if minutes is None else minutes)
                    if requested != stored:
                        raise BookCastError("内容模式或预算改变，请使用另一个 --output-dir，避免混用旧脚本。")
                elif mode is not None or minutes is not None:
                    raise BookCastError("旧任务保留原流水线；使用新的 --output-dir 创建分层内容任务。")
                if manifest.schema_version == 2 and manifest.config != config and not resume:
                    raise BookCastError("Provider 配置已改变。使用 --resume 保留已完成章节，或另选 --output-dir 创建新任务。")
                if not resume and manifest.status != "completed":
                    raise BookCastError(f"任务尚未完成，请使用 --resume 继续：{book_id}")
                if manifest.schema_version == 1:
                    backup = artifact_path(root, "manifest.v1.json")
                    if not backup.exists():
                        with atomic_target(backup) as temporary:
                            shutil.copyfile(manifest_path, temporary)
                    manifest.legacy_config = manifest.config.copy()
                    manifest.schema_version = 2
                    manifest.config = config
                    write_json(manifest_path, manifest.model_dump())
                elif manifest.config != config:
                    manifest.config = config
                    write_json(manifest_path, manifest.model_dump())
            else:
                if any(path.name != ".lock" for path in root.iterdir()):
                    raise BookCastError("目标任务目录非空且没有 manifest，拒绝覆盖已有数据。")
                manifest = Manifest(book_id=book_id, source_sha256=digest, source_name=source.name,
                                    source_format=source_format, config=config, pipeline_version="2",
                                    content_options=options.model_dump())
                write_json(manifest_path, manifest.model_dump())
            if revise_segment:
                plan_path = artifact_path(root, 'plans/episode.json')
                if manifest.pipeline_version != '2' or not plan_path.is_file():
                    raise BookCastError('当前任务没有可修订的全局规划。')
                from .content_models import EpisodePlan
                plan = EpisodePlan.model_validate_json(plan_path.read_text(encoding='utf-8'))
                if revise_segment not in {s.id for s in plan.segments}:
                    raise BookCastError('片段编号不在规划中。')
                manifest.segment_revisions[revise_segment] = manifest.segment_revisions.get(revise_segment, 0) + 1
                write_json(manifest_path, manifest.model_dump())
            runner = _Runner(root, manifest, self.llm, self.tts)
            try:
                runner.run(source, metadata_seed=metadata_seed)
            except (Exception, KeyboardInterrupt, SystemExit) as exc:
                manifest.status = "failed"
                manifest.error = str(exc) or "任务已中断"
                runner.save()
                if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                    raise
                raise BookCastError(f"任务 {book_id} 失败：{manifest.error}；修复原因后加 --resume 继续。") from exc
        return root


class _Runner:
    def __init__(self, root: Path, manifest: Manifest, llm: ProviderChain, tts: ProviderChain):
        self.root, self.manifest, self.llm, self.tts = root, manifest, llm, tts
        recovered = False
        for call in manifest.ai_calls:
            if call.status in {"pending", "running"}:
                call.status, call.error, call.retryable = "failed_retryable", "interrupted", True
                call.timestamp = utc_now()
                manifest.provider_status[f"{call.kind}:{call.provider}"] = ProviderStatus.from_error(
                    call.provider, call.model, ProviderError(ErrorKind.INTERRUPTED)).model_dump(mode="json")
                recovered = True
        llm.restore(manifest.ai_calls, "llm")
        tts.restore(manifest.ai_calls, "tts")
        if recovered:
            self.save()

    def path(self, relative: str) -> Path:
        return artifact_path(self.root, relative)

    def save(self) -> None:
        self.manifest.updated_at = utc_now()
        write_json(self.path("manifest.json"), self.manifest.model_dump())

    def step(self, name: str, inputs: object, operation: Callable[[], list[str]], *, legacy_inputs=None) -> None:
        key = fingerprint({"pipeline": self.manifest.pipeline_version, "step": name, "inputs": inputs})
        previous = self.manifest.steps.get(name)
        if previous and previous.status == "completed" and artifacts_valid(self.root, previous):
            if previous.fingerprint == key:
                return
            if self.manifest.legacy_config and legacy_inputs is not None:
                old_key = fingerprint({"pipeline": "1", "step": name, "inputs": legacy_inputs})
                if previous.fingerprint == old_key:
                    previous.fingerprint, previous.legacy = key, True
                    self.save()
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
        except (Exception, KeyboardInterrupt, SystemExit) as exc:
            record.status, record.error = "failed", str(exc) or "任务已中断"
            raise
        finally:
            record.updated_at = utc_now()
            self.save()

    def observe(self, attempt: AIAttempt) -> None:
        calls = self.manifest.ai_calls
        for index, existing in enumerate(calls):
            if existing.id == attempt.id:
                calls[index] = attempt.model_copy(deep=True)
                break
        else:
            calls.append(attempt.model_copy(deep=True))
        if attempt.error:
            report = ProviderStatus.from_error(attempt.provider, attempt.model, ProviderError(ErrorKind(attempt.error)))
        else:
            report = ProviderStatus(provider=attempt.provider, model=attempt.model,
                                    availability="available" if attempt.status == "completed" else "unknown")
        self.manifest.provider_status[f"{attempt.kind}:{attempt.provider}"] = report.model_dump(mode="json")
        self.save()

    def ai_operation(self, name: str, kind: str, version: str, inputs: object, invoke: Callable) -> list[str]:
        digest = fingerprint(inputs)
        # Recover the small window between AI completion and step completion.
        for call in reversed(self.manifest.ai_calls):
            if (call.task == name and call.status == "completed" and call.prompt_version == version
                    and call.input_hash == digest and artifacts_valid(self.root, call)):
                return list(call.artifacts)
        chain = self.llm if kind == "llm" else self.tts
        artifacts = chain.execute(task=name, kind=kind, prompt_version=version, input_hash=digest,
                                  invoke=invoke, persist=lambda names: {n: sha256_file(self.path(n)) for n in names},
                                  observe=self.observe)
        return list(artifacts)

    def run(self, source: Path, metadata_seed: BookMetadata | None = None) -> None:
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
            if metadata_seed:
                metadata = metadata_seed.model_copy(deep=True)
                metadata.book_id = manifest.book_id
                metadata.chapter_ids, metadata.warnings, metadata.coverage = [], [], "complete"
            book = parse_book(self.path(source_relative), metadata)
            outputs = []
            for chapter in book.chapters:
                name = f"chapters/{chapter.id}.json"
                write_json(self.path(name), chapter.model_dump())
                outputs.append(name)
            write_json(self.path("metadata.json"), book.metadata.model_dump())
            return ["metadata.json", *outputs]

        parse_inputs = {"source": sha256_file(self.path(source_relative)), "format": manifest.source_format}
        if metadata_seed:
            parse_inputs["metadata_seed"] = metadata_seed.model_dump()
        self.step("parse", parse_inputs, parse)
        metadata = BookMetadata.model_validate_json(self.path("metadata.json").read_text(encoding="utf-8"))
        manifest.warnings = metadata.warnings
        if manifest.pipeline_version == "2":
            from .content import ContentFlow
            ContentFlow(self, metadata).run()
            return
        legacy_config = manifest.legacy_config or {}
        for chapter_id in metadata.chapter_ids:
            chapter_name = f"chapters/{chapter_id}.json"
            chapter = Chapter.model_validate_json(self.path(chapter_name).read_text(encoding="utf-8"))
            analysis_name, script_name, audio_name = (f"analysis/{chapter_id}.json", f"scripts/{chapter_id}.json", f"audio/{chapter_id}.wav")

            prompt = analysis_prompt(chapter)
            analysis_inputs = {"prompt": prompt, "schema": ChapterAnalysis.model_json_schema()}

            def analyze(provider) -> list[str]:
                if not provider.capabilities().structured:
                    raise ProviderError(ErrorKind.INPUT)
                analysis = ChapterAnalysis.model_validate(provider.generate_structured(prompt, ChapterAnalysis))
                if analysis.chapter_id != chapter.id or analysis.source_locator != chapter.source_locator:
                    raise ProviderError(ErrorKind.BUSINESS)
                write_json(self.path(analysis_name), analysis.model_dump())
                return [analysis_name]

            self.step(f"analysis:{chapter_id}", analysis_inputs,
                      lambda: self.ai_operation(f"analysis:{chapter_id}", "llm", ANALYSIS_VERSION, analysis_inputs, analyze),
                      legacy_inputs={"chapter": sha256_file(self.path(chapter_name)), "llm": legacy_config.get("llm")})
            analysis = ChapterAnalysis.model_validate_json(self.path(analysis_name).read_text(encoding="utf-8"))

            prompt = script_prompt(chapter, analysis)
            script_inputs = {"prompt": prompt, "schema": PodcastScript.model_json_schema()}

            def script(provider) -> list[str]:
                if not provider.capabilities().structured:
                    raise ProviderError(ErrorKind.INPUT)
                result = PodcastScript.model_validate(provider.generate_structured(prompt, PodcastScript))
                if (result.chapter_id != chapter.id or result.source_locator != chapter.source_locator
                        or {turn.speaker for turn in result.turns} != {"主持人", "嘉宾"}):
                    raise ProviderError(ErrorKind.BUSINESS)
                write_json(self.path(script_name), result.model_dump())
                return [script_name]

            self.step(f"script:{chapter_id}", script_inputs,
                      lambda: self.ai_operation(f"script:{chapter_id}", "llm", SCRIPT_VERSION, script_inputs, script),
                      legacy_inputs={"analysis": sha256_file(self.path(analysis_name)),
                      "chapter": sha256_file(self.path(chapter_name)), "llm": legacy_config.get("llm")})
            podcast = PodcastScript.model_validate_json(self.path(script_name).read_text(encoding="utf-8"))

            tts_inputs = {"script": sha256_file(self.path(script_name)), "prompt_version": TTS_VERSION}

            def tts(provider) -> list[str]:
                if not provider.capabilities().speech:
                    raise ProviderError(ErrorKind.INPUT)
                with atomic_target(self.path(audio_name)) as temporary:
                    provider.synthesize(podcast, temporary)
                    try:
                        validate_wav(temporary)
                    except BookCastError:
                        raise ProviderError(ErrorKind.SCHEMA) from None
                return [audio_name]

            self.step(f"tts:{chapter_id}", tts_inputs,
                      lambda: self.ai_operation(f"tts:{chapter_id}", "tts", TTS_VERSION, tts_inputs, tts),
                      legacy_inputs={"script": sha256_file(self.path(script_name)), "tts": legacy_config.get("tts")})

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

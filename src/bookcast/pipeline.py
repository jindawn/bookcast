"""Chapter-granular checkpoints with input fingerprints and artifact hashes."""

from collections.abc import Callable
from pathlib import Path
import shutil
import os
import socket
import json
from uuid import uuid4

from .audio import merge_audio
from .errors import BookCastError
from .content_models import ContentOptions
from .models import (AIAttempt, Artifact, BookMetadata, Chapter, ChapterAnalysis, Manifest,
                     PodcastScript, RunOwner, StepRecord, TaskState, utc_now)
from .parsers import parse_book
from .provider_api import LLMProvider, TTSProvider, ProviderError, ProviderStatus, ErrorKind, classify_error
from .provider_chain import ProviderChain, provider_config_hash
from .prompts import ANALYSIS_VERSION, SCRIPT_VERSION, TTS_VERSION, analysis_prompt, script_prompt
from .source_validation import validate_source
from .storage import (artifact_path, atomic_target, cleanup_orphan_temporary_artifacts,
                      fingerprint, job_lock, sha256_file, write_json)


MAX_INPUT_BYTES = 100 * 1024 * 1024


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
    def __init__(self, llm: LLMProvider | ProviderChain, tts: TTSProvider | ProviderChain, output_dir: Path = Path("output"), *,
                 provider_settings: dict | None = None, progress: Callable[[dict], None] | None = None):
        self.llm = llm if isinstance(llm, ProviderChain) else ProviderChain([llm])
        self.tts = tts if isinstance(tts, ProviderChain) else ProviderChain([tts])
        from .speech import validate_tts_chain
        validate_tts_chain(self.tts)
        self.output_dir = output_dir.resolve()
        self.provider_settings, self.progress = provider_settings, progress

    def generate(self, source: Path, *, resume: bool = False, metadata_seed: BookMetadata | None = None,
                 mode: str | None = None, minutes: int | None = None, revise_segment: str | None = None,
                 _root: Path | None = None, _retry: bool = True, _by_id: bool = False) -> Path:
        if revise_segment and not resume:
            raise BookCastError("修订片段需要 --resume。")
        options = ContentOptions(mode="two_host" if mode is None else mode, minutes=10 if minutes is None else minutes)
        source = source.resolve()
        if not source.is_file():
            raise BookCastError(f"输入文件不存在：{source}")
        source_format = source.suffix.lower().lstrip(".")
        if source_format not in {"epub", "pdf", "txt"}:
            raise BookCastError("仅支持本地 EPUB、PDF 和 TXT 文件。")
        if source.stat().st_size > MAX_INPUT_BYTES:
            raise BookCastError("源文件超过 100 MiB 大小上限。")
        digest = sha256_file(source)
        if metadata_seed and (metadata_seed.source_sha256 != digest or metadata_seed.source_format != source_format):
            raise BookCastError("获取元数据与源文件不一致。")
        book_id = fingerprint({"source_sha256": digest, "format": source_format})[:24]
        root = _root.resolve() if _root else artifact_path(self.output_dir, book_id)
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
                cleanup_orphan_temporary_artifacts(root, manifest)
                if manifest.pipeline_version == "2":
                    stored = ContentOptions.model_validate(manifest.content_options)
                    requested = ContentOptions(mode=stored.mode if mode is None else mode, minutes=stored.minutes if minutes is None else minutes)
                    if requested != stored:
                        raise BookCastError("内容模式或预算改变，请使用另一个 --output-dir，避免混用旧脚本。")
                elif mode is not None or minutes is not None:
                    raise BookCastError("旧任务保留原流水线；使用新的 --output-dir 创建分层内容任务。")
                if (_by_id and not _retry and not revise_segment and (manifest.state == TaskState.FAILED_PERMANENT
                        or any(s.state == TaskState.FAILED_PERMANENT for s in manifest.steps.values()))):
                    raise BookCastError("任务存在永久失败；修复原因后使用 bookcast retry 显式重试。")
                if manifest.schema_version >= 2 and manifest.config != config and not resume:
                    raise BookCastError("Provider 配置已改变。使用 --resume 保留已完成章节，或另选 --output-dir 创建新任务。")
                if not resume and manifest.status != "completed":
                    raise BookCastError(f"任务尚未完成，请使用 --resume 继续：{book_id}")
                if manifest.schema_version < 3:
                    version = manifest.schema_version
                    backup = artifact_path(root, f"manifest.v{version}.json")
                    if not backup.exists():
                        with atomic_target(backup) as temporary:
                            shutil.copyfile(manifest_path, temporary)
                    if version == 1:
                        manifest.legacy_config = manifest.config.copy()
                    manifest.schema_version = 3
                    manifest.job_id = manifest.job_id or uuid4().hex
                    manifest.config = config
                    write_json(manifest_path, manifest.model_dump())
                elif manifest.config != config:
                    manifest.config = config
                    write_json(manifest_path, manifest.model_dump())
            else:
                cleanup_orphan_temporary_artifacts(root)
                if any(path.name != ".lock" and not (path.name.startswith(".manifest.json.")
                           and path.name.endswith(".tmp") and path.is_file() and not path.is_symlink())
                       for path in root.iterdir()):
                    raise BookCastError("目标任务目录非空且没有 manifest，拒绝覆盖已有数据。")
                manifest = Manifest(job_id=uuid4().hex, book_id=book_id, source_sha256=digest, source_name=source.name,
                                    source_path=str(source), metadata_seed=metadata_seed, provider_settings=self.provider_settings,
                                    source_format=source_format, config=config, pipeline_version="2",
                                    content_options=options.model_dump())
                write_json(manifest_path, manifest.model_dump())
            changed = False
            if self.provider_settings is not None and manifest.provider_settings != self.provider_settings:
                manifest.provider_settings = self.provider_settings
                changed = True
            if metadata_seed is not None and manifest.metadata_seed != metadata_seed:
                manifest.metadata_seed = metadata_seed
                changed = True
            metadata_seed = manifest.metadata_seed or metadata_seed
            if changed:
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
            runner = _Runner(root, manifest, self.llm, self.tts, progress=self.progress, by_id=_by_id)
            try:
                runner.run(source, metadata_seed=metadata_seed)
                runner.finish()
            except (Exception, KeyboardInterrupt, SystemExit) as exc:
                manifest.status = "failed"
                failure = classify_error(exc)
                manifest.error_kind = failure.kind.value
                manifest.error = str(exc) if isinstance(exc, BookCastError) else str(failure)
                runner.save()
                runner.event("job_failed", state=manifest.state.value, error=manifest.error_kind)
                if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                    raise
                raise BookCastError(f"任务 {manifest.job_id or book_id} 失败：{manifest.error}；可使用 resume 或 retry 继续。") from exc
            finally:
                if manifest.owner is not None:
                    manifest.owner = None
                    runner.save()
        return root

    def resume_job(self, root: Path, *, retry: bool = False, revise_segment: str | None = None) -> Path:
        root = root.resolve()
        manifest = load_manifest(artifact_path(root, 'manifest.json'))
        source = artifact_path(root, f'source/input.{manifest.source_format}')
        if not source.is_file() or sha256_file(source) != manifest.source_sha256:
            original = Path(manifest.source_path) if manifest.source_path else None
            if original is None or not original.is_file() or sha256_file(original) != manifest.source_sha256:
                raise BookCastError('恢复源文件缺失或损坏，且原始文件不可用；请提供相同内容的原文件。')
            source = original
        return self.generate(source, resume=True, metadata_seed=manifest.metadata_seed,
                             revise_segment=revise_segment, _root=root, _retry=retry, _by_id=True)



class _Runner:
    def __init__(self, root: Path, manifest: Manifest, llm: ProviderChain, tts: ProviderChain, *,
                 progress=None, by_id=False):
        self.root, self.manifest, self.llm, self.tts = root, manifest, llm, tts
        self.progress, self.by_id = progress, by_id
        self.visited_steps = set()
        self.log_enabled = manifest.status != 'completed'
        self.current_stage = None
        self.current_provider = None
        self.last_error = next((c.error for c in reversed(manifest.ai_calls) if c.error), None)
        self.session_id = uuid4().hex
        recovered = manifest.owner is not None or manifest.status == 'running'
        # Caller already owns the kernel lock. These records cannot belong to a live worker.
        manifest.owner = None
        for step in manifest.steps.values():
            if step.status == 'running':
                step.status, step.error_kind, step.error = 'failed_retryable', 'interrupted', 'interrupted'
                step.updated_at = utc_now()
                recovered = True
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
            manifest.status, manifest.error_kind, manifest.error = 'failed', 'interrupted', 'interrupted'
            self.log_enabled = True
            self.save()
            self.event('stale_recovered', state='FAILED_RETRYABLE', error='interrupted')

    def path(self, relative: str) -> Path:
        return artifact_path(self.root, relative)

    def save(self) -> None:
        self.manifest.updated_at = utc_now()
        write_json(self.path("manifest.json"), self.manifest.model_dump())

    def finish(self):
        changed = False
        for name, step in self.manifest.steps.items():
            if name not in self.visited_steps and step.status != 'skipped':
                step.status, step.skip_reason = 'skipped', 'not_in_current_plan'
                step.error, step.error_kind, step.updated_at = None, None, utc_now()
                changed = True
        if changed:
            self.save()
        self.event('job_finished', state='SUCCEEDED')

    def register(self, names, *, final=False):
        changed = False
        for name in names:
            if name not in self.manifest.steps:
                self.manifest.steps[name] = StepRecord()
                changed = True
        if final and not self.manifest.inventory_complete:
            self.manifest.inventory_complete = True
            changed = True
        if changed:
            self.save()

    def event(self, event, *, state=None, error=None):
        m = self.manifest
        completed = sum(s.status in {'completed', 'skipped'} for s in m.steps.values())
        chapter_ids = []
        title = m.metadata_seed.title if m.metadata_seed else Path(m.source_name).stem
        try:
            metadata = BookMetadata.model_validate_json(self.path('metadata.json').read_text(encoding='utf-8'))
            title, chapter_ids = metadata.title, metadata.chapter_ids
        except (OSError, ValueError):
            pass
        if error:
            self.last_error = error
        data = {'timestamp': utc_now(), 'event': event, 'job_id': m.job_id or m.book_id, 'book': title,
                'stage': self.current_stage, 'provider': self.current_provider,
                'state': state or m.state.value, 'completed': completed,
                'remaining': len(m.steps)-completed, 'total_final': m.inventory_complete,
                'chapters_completed': sum(m.steps.get(f'analysis:{cid}', StepRecord()).status in {'completed','skipped'}
                                          for cid in chapter_ids),
                'chapters_total': len(chapter_ids), 'error': error or m.error_kind or self.last_error}
        # Log I/O and a detached terminal must never convert durable success into a failed AI call.
        if self.log_enabled:
            try:
                path = self.path('logs/events.jsonl')
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open('a', encoding='utf-8') as stream:
                    stream.write(json.dumps(data, ensure_ascii=False) + '\n')
                    stream.flush()
            except OSError:
                pass  # manifest is the authoritative, fsynced journal
        if self.progress:
            try:
                self.progress(data)
            except Exception:
                pass

    def config_valid(self, call: AIAttempt) -> bool:
        if call.provider_config_hash is None:
            return True  # No fabricated configuration for pre-v3 attempts.
        chain = self.llm if call.kind == 'llm' else self.tts
        same_name = [p for p in chain.providers if p.name == call.provider]
        # Explicit replacement/failover may retain old results; an in-place configuration edit cannot.
        return not same_name or any(provider_config_hash(p, call.task) == call.provider_config_hash for p in same_name)

    def step_config_valid(self, name, record):
        for call in reversed(self.manifest.ai_calls):
            if call.task == name and call.status == 'completed' and call.artifacts == record.artifacts:
                return self.config_valid(call)
        return True

    def record_artifacts(self, name, record):
        call = next((a for a in reversed(self.manifest.ai_calls)
                     if a.task == name and a.status == 'completed' and a.artifacts == record.artifacts), None)
        for path, digest in record.artifacts.items():
            version = call.prompt_version if call else None
            config = call.provider_config_hash if call else None
            self.manifest.artifact_records[path] = Artifact(path=path, sha256=digest, step=name,
                input_hash=call.input_hash if call else record.input_hash or record.fingerprint, prompt_version=version,
                provider_config_hash=config, provider=call.provider if call else None, model=call.model if call else None,
                cache_key=fingerprint({'input_hash': call.input_hash if call else record.input_hash or record.fingerprint,
                                       'prompt_version': version, 'provider_configuration': config}),
                size_bytes=self.path(path).stat().st_size)

    def step(self, name: str, inputs: object, operation: Callable[[], list[str]], *, legacy_inputs=None) -> None:
        key = fingerprint({"pipeline": self.manifest.pipeline_version, "step": name, "inputs": inputs})
        previous = self.manifest.steps.get(name)
        self.current_stage = name
        self.visited_steps.add(name)
        self.current_provider = None
        if (previous and previous.status == "completed" and artifacts_valid(self.root, previous)
                and self.step_config_valid(name, previous)):
            if previous.fingerprint == key:
                call = next((c for c in reversed(self.manifest.ai_calls) if c.task == name and c.status == 'completed'), None)
                self.current_provider = call.provider if call else None
                if any(p not in self.manifest.artifact_records for p in previous.artifacts):
                    self.record_artifacts(name, previous)
                    self.save()
                self.event("cache_hit", state="SKIPPED")
                return
            if self.manifest.legacy_config and legacy_inputs is not None:
                old_key = fingerprint({"pipeline": "1", "step": name, "inputs": legacy_inputs})
                if previous.fingerprint == old_key:
                    previous.fingerprint, previous.legacy = key, True
                    self.record_artifacts(name, previous)
                    self.save()
                    return
        self.register([name])
        self.log_enabled = True
        if self.manifest.owner is None:
            self.manifest.owner = RunOwner(session_id=self.session_id, pid=os.getpid(), hostname=socket.gethostname())
        record = StepRecord(status="running", fingerprint=key, input_hash=fingerprint(inputs),
                            attempts=previous.attempts + 1 if previous else 1)
        self.manifest.steps[name] = record
        self.manifest.status, self.manifest.error, self.manifest.error_kind = "running", None, None
        self.save()
        self.event("step_started", state="RUNNING")
        try:
            outputs = operation()
            record.artifacts = {name: sha256_file(self.path(name)) for name in outputs}
            if not record.artifacts:
                raise BookCastError(f"步骤 {name} 没有产生文件。")
            self.record_artifacts(name, record)
            record.status = "completed"
        except (Exception, KeyboardInterrupt, SystemExit) as exc:
            failure = classify_error(exc)
            record.status = 'failed_retryable' if failure.retryable else 'failed_permanent'
            record.error_kind = failure.kind.value
            record.error = str(exc) if isinstance(exc, BookCastError) else str(failure)
            raise
        finally:
            record.updated_at = utc_now()
            self.save()
            self.event("step_finished", state=record.state.value, error=record.error_kind)

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
        self.current_provider = attempt.provider
        self.save()
        self.event("attempt", state=attempt.state.value, error=attempt.error)

    def ai_operation(self, name: str, kind: str, version: str, inputs: object, invoke: Callable) -> list[str]:
        digest = fingerprint(inputs)
        # Recover the small window between AI completion and step completion.
        for call in reversed(self.manifest.ai_calls):
            if (call.task == name and call.status == "completed" and call.prompt_version == version
                    and call.input_hash == digest and artifacts_valid(self.root, call) and self.config_valid(call)):
                return list(call.artifacts)
        chain = self.llm if kind == "llm" else self.tts
        artifacts = chain.execute(task=name, kind=kind, prompt_version=version, input_hash=digest,
                                  invoke=invoke, persist=lambda names: {n: sha256_file(self.path(n)) for n in names},
                                  observe=self.observe)
        return list(artifacts)

    def run(self, source: Path, metadata_seed: BookMetadata | None = None) -> None:
        manifest = self.manifest
        source_relative = f"source/input.{manifest.source_format}"
        self.register(['input', 'parse', 'merge', 'output'] +
                      (['claims', 'book_synthesis', 'plan', 'quality'] if manifest.pipeline_version == '2' else []))

        def import_source() -> list[str]:
            with atomic_target(self.path(source_relative)) as temporary:
                shutil.copyfile(source, temporary)
                if sha256_file(temporary) != manifest.source_sha256:
                    raise BookCastError("输入文件在读取期间发生变化，请重新运行。")
            return [source_relative]

        self.step("input", manifest.source_sha256, import_source)
        # A pre-audit parse checkpoint may have accepted an unsafe container.
        # Recheck the imported bytes before either parsing or reusing that checkpoint.
        validate_source(self.path(source_relative), manifest.source_format, MAX_INPUT_BYTES)

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
        old_parse = manifest.steps.get('parse')
        if (self.by_id and metadata_seed is None and old_parse and old_parse.status == 'completed'
                and artifacts_valid(self.root, old_parse)):
            # Pre-v3 acquired jobs did not retain the seed. Preserve a verified parse and its provenance.
            self.current_stage = 'parse'
            self.visited_steps.add('parse')
            if any(p not in manifest.artifact_records for p in old_parse.artifacts):
                self.record_artifacts('parse', old_parse)
                self.save()
            self.event('cache_hit', state='SKIPPED')
        else:
            if self.by_id and metadata_seed is None and self.path('metadata.json').is_file():
                metadata_seed = BookMetadata.model_validate_json(self.path('metadata.json').read_text(encoding='utf-8'))
                parse_inputs['metadata_seed'] = metadata_seed.model_dump()
            self.step("parse", parse_inputs, parse)
        metadata = BookMetadata.model_validate_json(self.path("metadata.json").read_text(encoding="utf-8"))
        manifest.warnings = metadata.warnings
        if manifest.pipeline_version == "2":
            from .content import ContentFlow
            ContentFlow(self, metadata).run()
            return
        self.register([f'{stage}:{cid}' for cid in metadata.chapter_ids for stage in ('analysis','script','tts')], final=True)
        legacy_config = manifest.legacy_config or {}
        for chapter_id in metadata.chapter_ids:
            chapter_name = f"chapters/{chapter_id}.json"
            chapter = Chapter.model_validate_json(self.path(chapter_name).read_text(encoding="utf-8"))
            analysis_name, script_name = f"analysis/{chapter_id}.json", f"scripts/{chapter_id}.json"

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
            from .speech import render_speech
            render_speech(self, podcast, tts_inputs, TTS_VERSION,
                          legacy_inputs={"script": sha256_file(self.path(script_name)), "tts": legacy_config.get("tts")})

        def merge() -> list[str]:
            with atomic_target(self.path("podcast.mp3")) as temporary:
                merge_audio(self.root, metadata.chapter_ids, temporary)
            return ["podcast.mp3"]

        audio_hashes = {cid: sha256_file(self.path(f"audio/{cid}.wav")) for cid in metadata.chapter_ids}
        self.step("merge", {"ordered_audio": list(audio_hashes.items()), "codec": "libmp3lame:96k"}, merge)

        def output() -> list[str]:
            from .speech import audio_summary
            write_json(self.path("audio/export.json"), {
                "book_id": manifest.book_id, "file": "podcast.mp3", "format": "mp3",
                "sha256": sha256_file(self.path("podcast.mp3")), "chapters": metadata.chapter_ids,
                "providers": manifest.config, "coverage": metadata.coverage, "warnings": metadata.warnings,
                **audio_summary(self, metadata.chapter_ids),
            })
            return ["audio/export.json"]

        self.step("output", {"mp3": sha256_file(self.path("podcast.mp3")), "metadata": sha256_file(self.path("metadata.json")), "audio_export": "v2"}, output)
        if manifest.status != "completed":
            manifest.status, manifest.error, manifest.error_kind = "completed", None, None
            self.save()


def job_status(job: str, output_dir: Path = Path("output")) -> dict:
    from .jobs import job_status as status
    return status(job, output_dir)

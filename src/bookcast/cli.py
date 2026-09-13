"""Typer is the composition root: providers are injected into the pipeline."""

import json
import shutil
import sys
from pathlib import Path
from typing import Annotated

import typer

from .errors import BookCastError
from .pipeline import Pipeline, job_status, load_manifest
from .provider_config import load_config
from .provider_registry import default_registry
from .provider_api import ProviderStatus, classify_error

app = typer.Typer(no_args_is_help=True, help="BookCast：本地电子书 → 可恢复的双人播客流水线。")
config_app = typer.Typer(help="查看 Provider 配置。")
app.add_typer(config_app, name="config")


@app.command()
def acquire(
    title: Annotated[str, typer.Argument(help="书名；支持不完整标题词组")],
    edition: Annotated[str | None, typer.Option(help="候选 ID，例如 gutenberg:3300")] = None,
    author: Annotated[str | None, typer.Option(help="作者筛选；用户来源时作为用户提供的元数据")] = None,
    language: Annotated[str | None, typer.Option(help="语言筛选，例如 en")] = None,
    list_only: Annotated[bool, typer.Option("--list", help="只显示身份候选，不获取书籍")] = False,
    source_provider: Annotated[str, typer.Option(help="目录来源注册名")] = "gutenberg",
    url: Annotated[str | None, typer.Option(help="用户有权获取的直接 HTTPS 文件 URL")] = None,
    file: Annotated[Path | None, typer.Option(help="用户自己的本地 EPUB/PDF/TXT")] = None,
    file_format: Annotated[str | None, typer.Option("--format", help="txt / epub / pdf；URL 必须指定")] = None,
    rights_confirmed: Annotated[bool, typer.Option(help="确认有权从用户 URL 获取并处理该文件")] = False,
    max_size_mb: Annotated[int, typer.Option(min=1, max=100, help="书籍文件大小上限 MiB")] = 32,
    download_timeout: Annotated[float, typer.Option(min=1, max=900, help="每个下载的总时限秒数；首次目录较大时可调高")] = 120,
    refresh_catalog: Annotated[bool, typer.Option(help="重新获取官方机器可读目录")] = False,
    resolve: Annotated[list[str] | None, typer.Option(help="可重复：主机名=已核验公网IP；保留原域名 TLS 校验")] = None,
    output_dir: Annotated[Path, typer.Option(help="获取产物与目录缓存根目录")] = Path("imports"),
    generate_audio: Annotated[bool, typer.Option("--generate", help="解析后接入原有 AI/音频流水线")] = False,
    pipeline_output_dir: Annotated[Path, typer.Option(help="音频流水线产物根目录")] = Path("output"),
    provider: Annotated[str, typer.Option(help="生成阶段 LLM 配置名或 auto")] = "auto",
    tts_provider: Annotated[str, typer.Option(help="生成阶段 TTS 配置名或 auto")] = "auto",
    config: Annotated[Path | None, typer.Option(help="生成阶段 Provider 配置")] = None,
    resume: Annotated[bool, typer.Option(help="继续未完成的音频生成任务；获取步骤自动复用检查点")] = False,
) -> None:
    """书名 → 明确版本 → 合法来源 → 安全获取 → 本地解析，默认不调用 AI。"""
    from .acquisition import Acquirer, choose_edition
    from .models import BookMetadata
    from .sources import source_registry
    from .source_http import SafeHTTP
    from .storage import artifact_path

    try:
        registry = source_registry()
        overrides = {}
        for value in resolve or []:
            if value.count("=") != 1:
                raise BookCastError("--resolve 格式应为主机名=公网IP。")
            host, address = value.split("=", 1)
            if host in overrides:
                raise BookCastError("--resolve 不允许重复主机。")
            overrides[host] = address
        http = SafeHTTP(address_overrides=overrides, total_timeout=download_timeout)
        if url or file:
            fmt = file_format or (file.suffix.lower().lstrip(".") if file else None)
            if fmt not in {"txt", "epub", "pdf"}:
                raise BookCastError("用户 URL 需指定 --format txt/epub/pdf；本地文件需为支持的格式。")
            source = registry.create("user", url=url, local_path=file, fmt=fmt, rights_confirmed=rights_confirmed)
        else:
            if source_provider == "user":
                raise BookCastError("user 来源需要 --url 或 --file。")
            source = registry.create(source_provider, cache_dir=artifact_path(output_dir.resolve(), ".catalog/gutenberg"),
                                     refresh=refresh_catalog, http=http)
        candidates = source.search(title, author=author, language=language)
        if list_only:
            typer.echo(candidates.model_dump_json(indent=2))
            return
        if not edition and (len(candidates.candidates) != 1 or not candidates.complete):
            typer.echo(candidates.model_dump_json(indent=2))
        selected = choose_edition(candidates, edition)
        offers = source.sources(selected)
        if file_format:
            offers = [offer for offer in offers if offer.format == file_format]
        if len(offers) != 1:
            raise BookCastError("该版本没有唯一的受支持来源格式；首个 Gutenberg 适配器只获取 UTF-8 TXT。")
        offer = offers[0]
        root = Acquirer(output_dir, http).acquire(selected, offer, max_bytes=max_size_mb * 1024 * 1024)
        metadata = BookMetadata.model_validate_json((root / "metadata.json").read_text(encoding="utf-8"))
        result = {"status": "parsed", "candidate": selected.model_dump(), "source": offer.model_dump(),
                  "directory": str(root), "file": str(root / "source" / f"input.{offer.format}"),
                  "chapters": len(metadata.chapter_ids), "coverage": metadata.coverage,
                  "warnings": [*candidates.warnings, *metadata.warnings]}
        if offer.jurisdiction == "US":
            result["warnings"].append("来源版权声明仅指美国公有领域；其他地区需核对当地适用条件。")
        if generate_audio:
            settings, ai_registry = load_config(config), default_registry()
            job = Pipeline(ai_registry.chain(settings, "llm", provider), ai_registry.chain(settings, "tts", tts_provider),
                           pipeline_output_dir).generate(Path(result["file"]), resume=resume, metadata_seed=metadata)
            result["pipeline_job"], result["podcast"] = str(job), str(job / "podcast.mp3")
        typer.echo(json.dumps(result, ensure_ascii=False, indent=2))
    except (BookCastError, OSError, ValueError) as exc:
        message = str(exc) if isinstance(exc, BookCastError) else "获取配置、文件或元数据无效。"
        typer.echo(f"错误：{message}", err=True)
        raise typer.Exit(1) from None


@app.command()
def generate(
    source: Annotated[Path, typer.Argument(help="用户提供的本地 EPUB / PDF / TXT")],
    resume: Annotated[bool, typer.Option(help="复用有效检查点，继续未完成的任务")] = False,
    output_dir: Annotated[Path, typer.Option(help="产物根目录")] = Path("output"),
    provider: Annotated[str, typer.Option(help="LLM 配置名称；auto 按优先级切换")] = "auto",
    tts_provider: Annotated[str, typer.Option(help="TTS 配置名称或 auto")] = "auto",
    config: Annotated[Path | None, typer.Option(help="Provider TOML 文件，默认 bookcast.toml")] = None,
) -> None:
    """生成 Mock 摘要、双人脚本和可播放的测试音调 MP3，无需 API Key。"""
    try:
        settings, registry = load_config(config), default_registry()
        root = Pipeline(registry.chain(settings, "llm", provider), registry.chain(settings, "tts", tts_provider),
                        output_dir).generate(source, resume=resume)
        manifest = load_manifest(root / "manifest.json")
    except (BookCastError, OSError) as exc:
        typer.echo(f"错误：{exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(f"任务完成：{root.name}\n音频：{root / 'podcast.mp3'}\n内置 Mock TTS 生成测试音调（非人声）。")
    for warning in manifest.warnings:
        typer.echo(f"解析警告：{warning}")


@config_app.command("providers")
def config_providers(
    config: Annotated[Path | None, typer.Option(help="Provider TOML 文件")] = None,
) -> None:
    """输出配置及注册类型，不读取密钥值、不连接服务。"""
    try:
        settings, registry = load_config(config), default_registry()
        # Validate factories as well as the TOML contract; constructors do no I/O.
        for spec in settings.providers:
            registry.create(spec)
        typer.echo(json.dumps({**settings.model_dump(mode="json"), "registered_types": registry.types()},
                              ensure_ascii=False, indent=2))
    except BookCastError as exc:
        typer.echo(f"错误：{exc}", err=True)
        raise typer.Exit(1) from None


@app.command()
def doctor(
    config: Annotated[Path | None, typer.Option(help="Provider TOML 文件")] = None,
) -> None:
    """检查运行环境和 Provider；兼容端点只查询 models，不生成内容。"""
    try:
        settings, registry = load_config(config), default_registry()
        reports = []
        for spec in settings.providers:
            instance = registry.create(spec)
            try:
                report = instance.health_check()
            except Exception as exc:
                report = ProviderStatus.from_error(spec.name, spec.model, classify_error(exc))
            reports.append({"kind": spec.kind, **report.model_dump(mode="json"),
                            "capabilities": instance.capabilities().model_dump()})
        ready = {kind: any(r["provider"] in priority and r["availability"] == "available"
                          and r["capabilities"][capability] for r in reports)
                 for kind, priority, capability in (("llm", settings.llm_priority, "structured"),
                                                    ("tts", settings.tts_priority, "speech"))}
        environment = {"python": sys.version.split()[0], "python_supported": sys.version_info >= (3, 12),
                       "ffmpeg": shutil.which("ffmpeg") is not None}
        healthy = all(ready.values()) and environment["python_supported"] and environment["ffmpeg"]
        typer.echo(json.dumps({"ready": healthy, "environment": environment, "chains": ready,
                              "providers": reports}, ensure_ascii=False, indent=2))
    except BookCastError as exc:
        typer.echo(f"错误：{exc}", err=True)
        raise typer.Exit(1) from None
    if not healthy:
        raise typer.Exit(1)


@app.command()
def status(
    job: Annotated[str, typer.Argument(help="book_id、任务目录或 manifest.json 路径")],
    output_dir: Annotated[Path, typer.Option(help="按 book_id 查找时使用的产物根目录")] = Path("output"),
    as_json: Annotated[bool, typer.Option("--json", help="输出机器可读 JSON")] = False,
) -> None:
    """只读查看任务检查点和产物完整性。"""
    try:
        result = job_status(job, output_dir)
    except (BookCastError, OSError) as exc:
        typer.echo(f"错误：{exc}", err=True)
        raise typer.Exit(1) from exc
    if as_json:
        typer.echo(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        typer.echo(f"任务：{result['book_id']}\n状态：{result['status']}\n产物完整性：{result['integrity']}")
        for name, record in result["steps"].items():
            typer.echo(f"  {name}: {record['status']}（尝试 {record['attempts']} 次）")
        if result["error"]:
            typer.echo(f"错误：{result['error']}")
        for warning in result["warnings"]:
            typer.echo(f"解析警告：{warning}")
        if result["damaged_steps"]:
            typer.echo(f"损坏步骤：{', '.join(result['damaged_steps'])}；请使用 --resume 修复。")

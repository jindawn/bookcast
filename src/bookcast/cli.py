"""Typer is the composition root: providers are injected into the pipeline."""

import json
import shutil
import sys
from pathlib import Path
from typing import Annotated

import typer

from . import composition
from .errors import BookCastError
from .pipeline import Pipeline, job_status, load_manifest
from .provider_config import load_config
from .provider_registry import default_registry
from .provider_api import ProviderStatus, classify_error
from .jobs import list_jobs, resolve_job

app = typer.Typer(no_args_is_help=True, help="BookCast：本地电子书 → 可恢复的双人播客流水线。")
config_app = typer.Typer(help="查看 Provider 配置。")
app.add_typer(config_app, name="config")
tts_app = typer.Typer(help="安装免费开源本地中文 TTS；生成阶段完全离线。")
app.add_typer(tts_app, name="tts")


@tts_app.command("setup")
def tts_setup(
    model_dir: Annotated[Path, typer.Option(help="模型安装父目录（约需 1 GB 可用空间）")] = Path("data/models"),
    archive: Annotated[Path | None, typer.Option(help="已下载的官方 tar.bz2，仍验证固定 SHA-256")] = None,
    config_output: Annotated[Path, typer.Option(help="新建配置文件；不会覆盖已有文件")] = Path("bookcast.toml"),
) -> None:
    """显式下载 Kokoro 中文模型并创建配置；不启用付费服务。"""
    from .tts_setup import install_model, write_local_config
    try:
        if config_output.exists():
            raise BookCastError("配置已存在；请用 --config-output 指定新的文件，安装不会覆盖配置。")
        typer.echo("安装 Kokoro 中文模型：下载约 350 MB，校验后本地合成，无 API 费用。")
        directory = install_model(model_dir, archive)
        write_local_config(config_output, directory)
        typer.echo(f"模型：{directory}\n配置：{config_output.resolve()}\n依赖：uv sync --extra tts\n"
                   "请使用 bookcast doctor --config <配置路径> 检查；LLM 仍为 Mock，可单独调整。")
    except (BookCastError, OSError) as exc:
        typer.echo(f"错误：{exc}", err=True)
        raise typer.Exit(1) from None


@app.command()
def serve(
    port: Annotated[int, typer.Option(min=1024, max=65535, help="本地 HTTP 端口")] = 8765,
    data_dir: Annotated[Path, typer.Option(help="Web 提交、上传、获取和 Core 产物目录")] = Path('data/web'),
    ui_dir: Annotated[Path, typer.Option(help="Next.js 静态导出目录")] = Path('web/out'),
    config: Annotated[Path | None, typer.Option(help="服务启动时指定 Provider 配置")] = None,
) -> None:
    """启动本地 Web/API；始终仅绑定 127.0.0.1，无需账户。"""
    try:
        import uvicorn
        from .web_api import create_app
    except ImportError:
        typer.echo('请先安装 Web 依赖：uv sync --extra web', err=True)
        raise typer.Exit(1) from None
    uvicorn.run(create_app(data_dir, config, ui_dir.resolve()), host='127.0.0.1', port=port)


def show_progress(event):
    def clean(value):
        return ''.join(c for c in str(value if value is not None else '-') if c.isprintable())[:160]
    if event['event'] == 'attempt' and event['state'] == 'PENDING':
        return
    suffix = '' if event['total_final'] else '（后续任务数待规划）'
    typer.echo(f"[{clean(event['state'])}] {clean(event['book'])} | {clean(event['stage'])} | "
               f"章节 {event['chapters_completed']}/{event['chapters_total']} | Provider {clean(event['provider'])} | "
               f"完成 {event['completed']} 剩余 {event['remaining']}{suffix} | 最近错误 {clean(event['error'])}", err=True)


def resume_pipeline(manifest, root, config, provider, tts_provider):
    return composition.resume_pipeline(manifest, root, config, provider, tts_provider, progress=show_progress)


def generation_pipeline(source, output_dir, config, provider, tts_provider, resume):
    return composition.generation_pipeline(source, output_dir, config, provider, tts_provider, resume,
                                           progress=show_progress)


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
    mode: Annotated[str | None, typer.Option(help="生成模式：summary / deep_read / two_host")] = None,
    minutes: Annotated[int | None, typer.Option(min=1, max=120, help="生成脚本的目标分钟数")] = None,
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
            pipeline = generation_pipeline(Path(result['file']), pipeline_output_dir, config, provider, tts_provider, resume)
            job = pipeline.generate(Path(result['file']), resume=resume, metadata_seed=metadata, mode=mode, minutes=minutes)
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
    mode: Annotated[str | None, typer.Option(help="summary / deep_read / two_host；新任务默认 two_host")] = None,
    minutes: Annotated[int | None, typer.Option(min=1, max=120, help="脚本目标分钟数；新任务默认10")] = None,
    revise_segment: Annotated[str | None, typer.Option(help="与 --resume 配合，重新生成指定片段及受影响下游")] = None,
) -> None:
    """分块分析、全书综合、节目规划、脚本复核与音频；默认 Mock 离线运行。"""
    try:
        pipeline = generation_pipeline(source, output_dir, config, provider, tts_provider, resume)
        root = pipeline.generate(source, resume=resume, mode=mode, minutes=minutes, revise_segment=revise_segment)
        manifest = load_manifest(root / "manifest.json")
    except (BookCastError, OSError, ValueError) as exc:
        typer.echo(f"错误：{exc}", err=True)
        raise typer.Exit(1) from exc
    export = json.loads((root / "audio/export.json").read_text(encoding="utf-8"))
    typer.echo(f"任务完成：{manifest.job_id or root.name}\n音频：{root / 'podcast.mp3'}\n{export.get('note', '请检查音频来源记录。')}")
    if manifest.pipeline_version == "2":
        report = json.loads((root / "evaluation/quality.json").read_text(encoding="utf-8"))
        typer.echo(f"质量报告：{root / 'evaluation/quality.json'}（{report['status']}）")
        for warning in report['warnings']:
            typer.echo(f"内容提示：{warning}")
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
    output_dir: Annotated[Path, typer.Option(help="检查任务存储根目录")] = Path("output"),
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
                            **({"action": "运行 uv sync --extra tts；用 bookcast tts setup 安装并校验模型，核对 local_tts.model_dir。"}
                               if spec.type == "kokoro-local" and report.availability != "available" else {}),
                            "capabilities": instance.capabilities().model_dump()})
        ready = {kind: any(r["provider"] in priority and r["availability"] == "available"
                          and r["capabilities"][capability] for r in reports)
                 for kind, priority, capability in (("llm", settings.llm_priority, "structured"),
                                                    ("tts", settings.tts_priority, "speech"))}
        environment = {"python": sys.version.split()[0], "python_supported": sys.version_info >= (3, 12),
                       "ffmpeg": shutil.which("ffmpeg") is not None}
        healthy = all(ready.values()) and environment["python_supported"] and environment["ffmpeg"]
        inventory = list_jobs(output_dir)
        job_health = {'total': len(inventory['jobs']), 'active': sum(j['active'] for j in inventory['jobs']),
                      'stale': [j['job_id'] for j in inventory['jobs'] if j['stale']],
                      'corrupt': inventory['errors'], 'output_dir': str(output_dir.resolve()),
                      'recovery': 'stale 任务使用 resume；永久错误修复后使用 retry。'}
        typer.echo(json.dumps({"ready": healthy, "environment": environment, "chains": ready, 'jobs': job_health,
                              "providers": reports}, ensure_ascii=False, indent=2))
    except BookCastError as exc:
        typer.echo(f"错误：{exc}", err=True)
        raise typer.Exit(1) from None
    if not healthy:
        raise typer.Exit(1)


@app.command()
def status(
    job: Annotated[str, typer.Argument(help="Job ID、book_id、任务目录或 manifest.json 路径")],
    output_dir: Annotated[Path, typer.Option(help="按 ID 查找时使用的产物根目录")] = Path("output"),
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
        typer.echo(f"任务：{result['job_id']}\n书籍：{result['progress']['book']}\n状态：{result['effective_state']}"
                   f"\n进程持锁：{result['active']}；stale：{result['stale']}\n产物完整性：{result['integrity']}")
        show_progress({'event':'status', 'state':result['effective_state'], **result['progress']})
        for name, record in result["steps"].items():
            typer.echo(f"  {name}: {record['status']}（尝试 {record['attempts']} 次）")
        if result["error"]:
            typer.echo(f"错误：{result['error']}")
        for warning in result["warnings"]:
            typer.echo(f"解析警告：{warning}")
        if result["damaged_steps"]:
            typer.echo(f"损坏步骤：{', '.join(result['damaged_steps'])}；请使用 bookcast resume 修复。")


@app.command('jobs')
def jobs_command(
    output_dir: Annotated[Path, typer.Option(help='任务存储根目录，包含其子目录')] = Path('output'),
    as_json: Annotated[bool, typer.Option('--json', help='输出机器可读任务列表')] = False,
) -> None:
    """列出持久化任务，不修改或自动重试任务。"""
    result = list_jobs(output_dir)
    if as_json:
        typer.echo(json.dumps(result, ensure_ascii=False, indent=2))
        return
    if not result['jobs']:
        typer.echo('没有找到任务。')
    for job in result['jobs']:
        p = job['progress']
        typer.echo(f"{job['job_id']}  {job['effective_state']}  {p['book']}  "
                   f"完成 {p['completed']} / 已知 {p['completed']+p['remaining']}  {job['directory']}")
    for error in result['errors']:
        typer.echo(f"无效任务：{error['directory']}；{error['error']}",err=True)


def continue_job(job, output_dir, config, provider, tts_provider, *, retry, revise_segment=None):
    try:
        path = resolve_job(job, output_dir)
        manifest = load_manifest(path)
        pipeline = resume_pipeline(manifest, path.parent, config, provider, tts_provider)
        root = pipeline.resume_job(path.parent, retry=retry, revise_segment=revise_segment)
        manifest = load_manifest(root/'manifest.json')
        typer.echo(f"任务完成：{manifest.job_id or manifest.book_id}\n目录：{root}\n音频：{root/'podcast.mp3'}")
    except (BookCastError, OSError, ValueError) as exc:
        message = str(exc) if isinstance(exc, BookCastError) else '任务文件或配置无效。'
        typer.echo(f'错误：{message}',err=True)
        raise typer.Exit(1) from None


@app.command('resume')
def resume_command(
    job: Annotated[str, typer.Argument(help='Job ID 或任务目录')],
    output_dir: Annotated[Path, typer.Option(help='任务存储根目录')] = Path('output'),
    config: Annotated[Path | None, typer.Option(help='显式替换保存的 Provider 配置')] = None,
    provider: Annotated[str | None, typer.Option(help='LLM 配置名或 auto')] = None,
    tts_provider: Annotated[str | None, typer.Option(help='TTS 配置名或 auto')] = None,
) -> None:
    """从导入副本恢复 pending、stale 或临时失败任务；保留已完成产物。"""
    continue_job(job,output_dir,config,provider,tts_provider,retry=False)


@app.command('retry')
def retry_command(
    job: Annotated[str, typer.Argument(help='Job ID 或任务目录')],
    output_dir: Annotated[Path, typer.Option(help='任务存储根目录')] = Path('output'),
    config: Annotated[Path | None, typer.Option(help='修复或替换 Provider 配置')] = None,
    provider: Annotated[str | None, typer.Option(help='LLM 配置名或 auto')] = None,
    tts_provider: Annotated[str | None, typer.Option(help='TTS 配置名或 auto')] = None,
    revise_segment: Annotated[str | None, typer.Option(help='显式改写质量失败的片段')] = None,
) -> None:
    """原因修复后显式重试永久失败；不从头生成已完成章节。"""
    continue_job(job,output_dir,config,provider,tts_provider,retry=True,revise_segment=revise_segment)

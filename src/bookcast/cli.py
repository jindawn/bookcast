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

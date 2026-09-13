"""Typer is the composition root: providers are injected into the pipeline."""

import json
from pathlib import Path
from typing import Annotated

import typer

from .errors import BookCastError
from .pipeline import Pipeline, job_status, load_manifest
from .providers import MockLLMProvider, MockTTSProvider

app = typer.Typer(no_args_is_help=True, help="BookCast：本地电子书 → 可恢复的 Mock 双人播客。")


@app.command()
def generate(
    source: Annotated[Path, typer.Argument(help="用户提供的本地 EPUB / PDF / TXT")],
    resume: Annotated[bool, typer.Option(help="复用有效检查点，继续未完成的任务")] = False,
    output_dir: Annotated[Path, typer.Option(help="产物根目录")] = Path("output"),
) -> None:
    """生成 Mock 摘要、双人脚本和可播放的测试音调 MP3，无需 API Key。"""
    try:
        root = Pipeline(MockLLMProvider(), MockTTSProvider(), output_dir).generate(source, resume=resume)
        manifest = load_manifest(root / "manifest.json")
    except (BookCastError, OSError) as exc:
        typer.echo(f"错误：{exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(f"任务完成：{root.name}\nMock 测试音调（非人声）：{root / 'podcast.mp3'}")
    for warning in manifest.warnings:
        typer.echo(f"解析警告：{warning}")


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

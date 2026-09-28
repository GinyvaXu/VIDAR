"""BiliVideoKing CLI 入口。"""

from __future__ import annotations

import contextlib
import json
import sys
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from . import __version__
from .config import (
    Settings,
    default_config_path,
    dump_settings,
    load_settings,
    write_default_config,
)
from .doctor import STATUS_FAIL, STATUS_OK, STATUS_WARN, run_checks
from .errors import BilikingError
from .logging_setup import setup_logging
from .pipeline import PipelineRunner, RunContext, default_steps, resolve_path

app = typer.Typer(
    help="BiliVideoKing：把 B 站视频变成可读、可检索、可追溯的 Markdown 知识文档。",
    no_args_is_help=True,
    add_completion=False,
    pretty_exceptions_enable=False,
)
config_app = typer.Typer(help="配置管理", no_args_is_help=True)
app.add_typer(config_app, name="config")

console = Console()


def _load(config: Path | None, overrides: dict[str, Any] | None = None) -> Settings:
    return load_settings(config, overrides)


def _setup_logs(settings: Settings, *, verbose: bool = False, quiet: bool = False) -> None:
    logs_dir = resolve_path(settings.paths.logs_dir)
    setup_logging(logs_dir, verbose=verbose, quiet=quiet)


def _fail(exc: BilikingError, code: int = 1) -> None:
    console.print(f"[bold red]✗ {exc.message}[/bold red]")
    if exc.hint:
        console.print(f"[dim]{exc.hint}[/dim]")
    raise typer.Exit(code)


# --------------------------------------------------------------------------- #
# run
# --------------------------------------------------------------------------- #
@app.command()
def run(
    source: str = typer.Argument(..., help="BV 号或视频 URL"),
    config: Path | None = typer.Option(None, "--config", help="指定配置文件路径"),
    out: Path | None = typer.Option(None, "--out", help="输出根目录（覆盖 paths.output_dir）"),
    asr_model: str | None = typer.Option(None, "--asr-model", help="ASR 模型档位，如 large-v3"),
    device: str | None = typer.Option(None, "--device", help="auto | cuda | cpu"),
    llm_model: str | None = typer.Option(None, "--llm-model", help="LLM 模型名"),
    keep_media: bool = typer.Option(False, "--keep-media", help="保留下载的音视频文件"),
    keep_audio: bool = typer.Option(False, "--keep-audio", help="仅保留音频文件"),
    from_step: str | None = typer.Option(None, "--from", help="从指定步骤开始（重置该步及后续）"),
    to_step: str | None = typer.Option(None, "--to", help="执行到指定步骤为止"),
    dry_run: bool = typer.Option(False, "--dry-run", help="只展示执行计划，不实际执行"),
    verbose: bool = typer.Option(False, "-v", "--verbose", help="输出详细日志"),
) -> None:
    """处理一条视频：解析 → 下载 → 识别 → 文稿 → 总结 → 导出。"""
    overrides: dict[str, Any] = {}
    if out is not None:
        overrides.setdefault("paths", {})["output_dir"] = str(out)
    if asr_model:
        overrides.setdefault("asr", {})["model"] = asr_model
    if device:
        overrides.setdefault("asr", {})["device"] = device
    if llm_model:
        overrides.setdefault("llm", {})["model"] = llm_model
    if keep_media:
        overrides.setdefault("export", {})["keep_media"] = True
    if keep_audio:
        overrides.setdefault("export", {})["keep_audio"] = True

    try:
        settings = _load(config, overrides)
        _setup_logs(settings, verbose=verbose)
        ctx = RunContext(settings, source)
    except BilikingError as exc:
        _fail(exc, code=2)

    llm_text = (
        f"{settings.llm.model} @ {settings.llm.api_base}"
        if settings.llm.configured
        else "未配置（M2 总结前完成配置即可）"
    )
    console.print(
        Panel.fit(
            f"[bold]源[/bold]　　　{ctx.source}\n"
            f"[bold]任务ID[/bold]　{ctx.key}\n"
            f"[bold]工作目录[/bold] {ctx.work_dir}\n"
            f"[bold]配置文件[/bold] {settings.config_path or '（全部默认值）'}\n"
            f"[bold]ASR[/bold]　　 {settings.asr.engine} / "
            f"{settings.asr.model} / {settings.asr.device}\n"
            f"[bold]LLM[/bold]　　 {llm_text}",
            title="BiliVideoKing",
            border_style="cyan",
        )
    )

    runner = PipelineRunner(ctx, default_steps())  # type: ignore[arg-type]
    try:
        runner.run(from_step=from_step, to_step=to_step, dry_run=dry_run)
    except BilikingError as exc:
        _fail(exc)

    if not dry_run:
        table = Table(title="步骤状态", show_header=True)
        table.add_column("步骤", style="cyan", no_wrap=True)
        table.add_column("内容")
        table.add_column("状态")
        for name, title, status in runner.summary_rows():
            table.add_row(name, title, status)
        console.print(table)


# --------------------------------------------------------------------------- #
# doctor
# --------------------------------------------------------------------------- #
@app.command()
def doctor(
    config: Path | None = typer.Option(None, "--config", help="指定配置文件路径"),
    net: bool = typer.Option(False, "--net", help="额外检查 B 站与 LLM API 连通性"),
) -> None:
    """环境自检：依赖、硬件、配置、目录、磁盘。"""
    try:
        settings = _load(config)
        _setup_logs(settings, quiet=True)
    except BilikingError as exc:
        _fail(exc, code=2)

    results = run_checks(settings, net=net)

    icons = {
        STATUS_OK: "[green]✓[/green]",
        STATUS_WARN: "[yellow]![/yellow]",
        STATUS_FAIL: "[red]✗[/red]",
    }
    title = f"环境自检 · 配置文件：{settings.config_path or '（默认值）'}"
    table = Table(title=title, show_lines=False)
    table.add_column("检查项", style="cyan", no_wrap=True)
    table.add_column("结果", no_wrap=True)
    table.add_column("说明")
    table.add_column("建议", style="dim")
    for item in results:
        table.add_row(item.name, icons.get(item.status, item.status), item.detail, item.hint)
    console.print(table)

    failures = [item for item in results if item.status == STATUS_FAIL]
    warnings = [item for item in results if item.status == STATUS_WARN]
    if failures:
        console.print(f"[red]✗ {len(failures)} 项必须解决[/red]")
        raise typer.Exit(1)
    if warnings:
        console.print(f"[yellow]! {len(warnings)} 项警告（不阻塞基本流程）[/yellow]")
    else:
        console.print("[green]✓ 全部检查通过[/green]")


# --------------------------------------------------------------------------- #
# config
# --------------------------------------------------------------------------- #
@config_app.command("init")
def config_init(
    path: Path | None = typer.Option(None, "--path", help="写入路径（默认用户配置目录）"),
    force: bool = typer.Option(False, "--force", help="覆盖已存在的文件"),
) -> None:
    """生成默认配置文件。"""
    target = path or default_config_path()
    try:
        written = write_default_config(target, force=force)
    except BilikingError as exc:
        _fail(exc)
    console.print(f"[green]✓ 已写入配置：{written}[/green]")
    console.print("[dim]编辑后可用 biliking config show 查看生效配置[/dim]")


@config_app.command("show")
def config_show(
    config: Path | None = typer.Option(None, "--config", help="指定配置文件路径"),
    as_json: bool = typer.Option(False, "--json", help="以 JSON 输出"),
) -> None:
    """展示生效配置（密钥掩码）。"""
    try:
        settings = _load(config)
    except BilikingError as exc:
        _fail(exc, code=2)
    data = dump_settings(settings)
    console.print(f"[dim]配置文件：{settings.config_path or '（全部默认值）'}[/dim]")
    if as_json:
        console.print_json(json.dumps(data, ensure_ascii=False))
    else:
        import tomli_w

        console.print(tomli_w.dumps(data))


@app.command()
def version() -> None:
    """显示版本号。"""
    console.print(f"BiliVideoKing v{__version__}")


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        with contextlib.suppress(Exception):  # 个别终端不支持，忽略
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    app()


if __name__ == "__main__":
    main()

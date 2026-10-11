"""VIDAR CLI 入口。"""

from __future__ import annotations

import contextlib
import json
import os
import sys
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    DownloadColumn,
    Progress,
    TextColumn,
    TimeRemainingColumn,
    TransferSpeedColumn,
)
from rich.table import Table

from . import __version__
from .config import (
    Settings,
    default_config_path,
    dump_settings,
    exe_dir,
    load_settings,
    write_default_config,
)
from .doctor import STATUS_FAIL, STATUS_OK, STATUS_WARN, run_checks
from .errors import CancelledError, VidarError
from .logging_setup import setup_logging
from .pipeline import PipelineRunner, RunContext, default_steps, resolve_path

app = typer.Typer(
    help="VIDAR：把 B 站视频变成可读、可检索、可追溯的 Markdown 知识文档。",
    no_args_is_help=True,
    add_completion=False,
    pretty_exceptions_enable=False,
)
config_app = typer.Typer(help="配置管理", no_args_is_help=True)
app.add_typer(config_app, name="config")
model_app = typer.Typer(help="ASR 模型辅助下载与校验", no_args_is_help=True)
app.add_typer(model_app, name="model")
gpu_app = typer.Typer(help="GPU（CUDA）运行时辅助安装", no_args_is_help=True)
app.add_typer(gpu_app, name="gpu")
llm_app = typer.Typer(help="LLM 接入引导：服务商预设 / 连通性测试 / 一键配置", no_args_is_help=True)
app.add_typer(llm_app, name="llm")

console = Console()


def _load(config: Path | None, overrides: dict[str, Any] | None = None) -> Settings:
    return load_settings(config, overrides)


def _setup_logs(settings: Settings, *, verbose: bool = False, quiet: bool = False) -> None:
    logs_dir = resolve_path(settings.paths.logs_dir)
    setup_logging(logs_dir, verbose=verbose, quiet=quiet)


def _fail(exc: VidarError, code: int = 1) -> None:
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
    pdf: bool = typer.Option(False, "--pdf", help="导出 PDF（阅读/打印，默认报刊风）"),
    docx: bool = typer.Option(False, "--docx", help="导出 Word（.docx，可再编辑）"),
    epub: bool = typer.Option(False, "--epub", help="导出 EPUB 电子书（微信读书/墨水屏）"),
    pdf_style: str | None = typer.Option(
        None, "--pdf-style", help="PDF 风格：newspaper 报刊风 | paper 论文风"
    ),
    pdf_mode: str | None = typer.Option(
        None, "--pdf-mode", help="PDF 篇幅：full 全文 | brief 精简"
    ),
    pdf_scale: str | None = typer.Option(
        None, "--pdf-scale", help="字号档位：compact 小 | standard 标准 | large 大"
    ),
    point_figures: str | None = typer.Option(
        None, "--point-figures", help="要点截图密度：all 全部 | sparse 隔一取一 | off 关闭"
    ),
    screenshots: bool = typer.Option(
        True, "--screenshots/--no-screenshots", help="关键帧截图（供 docx/pdf 配图）"
    ),
    translate: bool = typer.Option(
        True, "--translate/--no-translate", help="外语视频自动双语翻译"
    ),
    ads: bool = typer.Option(
        True, "--ads/--no-ads", help="LLM 识别广告段落并从成品省略（另存省略记录）"
    ),
    target_lang: str | None = typer.Option(
        None, "--target-lang", help="翻译目标语言：zh 中文 | en 英文 | ja 日文 …"
    ),
    bilingual: bool = typer.Option(
        True, "--bilingual/--no-bilingual", help="双语排版（译文正文 + 原文小字）"
    ),
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
    if pdf or pdf_style or pdf_mode:
        overrides.setdefault("export", {})["pdf"] = True
    if docx:
        overrides.setdefault("export", {})["docx"] = True
    if epub:
        overrides.setdefault("export", {})["epub"] = True
    if pdf_style:
        overrides.setdefault("export", {})["pdf_style"] = pdf_style
    if pdf_mode:
        overrides.setdefault("export", {})["pdf_mode"] = pdf_mode
    if pdf_scale:
        overrides.setdefault("export", {})["pdf_scale"] = pdf_scale
    if point_figures:
        overrides.setdefault("export", {})["point_figures"] = point_figures
    if not screenshots:
        overrides.setdefault("export", {})["screenshots"] = False
    if not translate:
        overrides.setdefault("translate", {})["enabled"] = False
    if not ads:
        overrides.setdefault("ads", {})["enabled"] = False
    if target_lang:
        overrides.setdefault("translate", {})["target_language"] = target_lang
    if not bilingual:
        overrides.setdefault("export", {})["bilingual"] = False

    try:
        settings = _load(config, overrides)
        _setup_logs(settings, verbose=verbose)
        ctx = RunContext(settings, source)
    except VidarError as exc:
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
            f"[bold]LLM[/bold]　　 {llm_text}\n"
            f"[bold]翻译[/bold]　　"
            + (
                f"开启（→ {settings.translate.target_language}）"
                if settings.translate.enabled
                else "关闭"
            )
            + "\n"
            "[bold]广告识别[/bold]　"
            + ("开启（成品省略 + 记录留档）" if settings.ads.enabled else "关闭"),
            title="VIDAR",
            border_style="cyan",
        )
    )

    runner = PipelineRunner(ctx, default_steps())  # type: ignore[arg-type]
    try:
        runner.run(from_step=from_step, to_step=to_step, dry_run=dry_run)
    except CancelledError as exc:
        _fail(exc, code=130)
    except VidarError as exc:
        _fail(exc)

    if not dry_run:
        table = Table(title="步骤状态", show_header=True)
        table.add_column("步骤", style="cyan", no_wrap=True)
        table.add_column("内容")
        table.add_column("状态")
        for name, title, status in runner.summary_rows():
            table.add_row(name, title, status)
        console.print(table)


def _open_path(path: Path) -> None:
    """用系统默认程序打开文件/目录。"""
    import subprocess

    if sys.platform == "win32":
        os.startfile(str(path))  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


# --------------------------------------------------------------------------- #
# pdf（单独重渲染，快速调试排版）
# --------------------------------------------------------------------------- #
@app.command()
def pdf(
    source: str = typer.Argument(..., help="BV 号或视频 URL（需已完整跑过一次）"),
    config: Path | None = typer.Option(None, "--config", help="指定配置文件路径"),
    style: str | None = typer.Option(None, "--style", help="newspaper 报刊风 | paper 论文风"),
    mode: str | None = typer.Option(None, "--mode", help="full 全文 | brief 精简"),
    scale: str | None = typer.Option(
        None, "--scale", help="字号档位：compact 小 | standard 标准 | large 大（或 0.6–1.5 数字）"
    ),
    point_figures: str | None = typer.Option(
        None, "--point-figures", help="要点截图：all 全部 | sparse 隔一取一 | off 关闭"
    ),
    thumb_mm: float | None = typer.Option(None, "--thumb-mm", help="要点缩略图宽度（mm，默认 28）"),
    bilingual: bool | None = typer.Option(
        None, "--bilingual/--no-bilingual", help="双语排版（默认跟随配置 export.bilingual）"
    ),
    docx: bool = typer.Option(False, "--docx", help="同时重导 Word"),
    open_file: bool = typer.Option(False, "--open", help="完成后用系统默认程序打开 PDF"),
) -> None:
    """单独重渲染 PDF（风格/篇幅/字号/要点截图密度），不重跑流水线，适合反复调试。"""
    from .rerender import rerender_task

    try:
        settings = _load(config)
        _setup_logs(settings, quiet=True)
        artifacts = rerender_task(
            settings,
            source,
            style=style,
            mode=mode,
            scale=scale,
            point_figures=point_figures,
            point_thumb_mm=thumb_mm,
            docx=docx,
            bilingual=bilingual,
        )
    except VidarError as exc:
        _fail(exc)
        return

    for kind, path in artifacts.items():
        console.print(f"[green]✓[/green] {kind} → [cyan]{path}[/cyan]")
    if open_file and "pdf" in artifacts:
        _open_path(artifacts["pdf"])


# --------------------------------------------------------------------------- #
# epub（单独重导出电子书）
# --------------------------------------------------------------------------- #
@app.command()
def epub(
    source: str = typer.Argument(..., help="BV 号或视频 URL（需已完整跑过一次）"),
    config: Path | None = typer.Option(None, "--config", help="指定配置文件路径"),
    mode: str | None = typer.Option(None, "--mode", help="full 全文 | brief 精简"),
    bilingual: bool | None = typer.Option(
        None, "--bilingual/--no-bilingual", help="双语排版（默认跟随配置 export.bilingual）"
    ),
    open_file: bool = typer.Option(False, "--open", help="完成后打开 EPUB"),
) -> None:
    """单独重导出 EPUB 电子书（微信读书 / 电子墨水阅读器），不重跑流水线。"""
    from .rerender import rerender_task

    try:
        settings = _load(config)
        _setup_logs(settings, quiet=True)
        artifacts = rerender_task(settings, source, mode=mode, epub=True, bilingual=bilingual)
    except VidarError as exc:
        _fail(exc)
        return

    for kind, path in artifacts.items():
        console.print(f"[green]✓[/green] {kind} → [cyan]{path}[/cyan]")
    console.print(
        "[dim]导入方法：微信读书 →「我的」上方搜索「传书到手机」"
        "（或网页版 reader.qq.com）→ 上传该 EPUB 文件[/dim]"
    )
    if open_file and "epub" in artifacts:
        _open_path(artifacts["epub"])


# --------------------------------------------------------------------------- #
# batch（批量任务队列）
# --------------------------------------------------------------------------- #
@app.command()
def batch(
    file: Path = typer.Argument(..., help="链接清单文件（每行一个 BV 号/链接，# 注释）"),
    config: Path | None = typer.Option(None, "--config", help="指定配置文件路径"),
    from_step: str | None = typer.Option(None, "--from", help="从指定步骤开始（重置该步及后续）"),
    to_step: str | None = typer.Option(None, "--to", help="执行到指定步骤为止"),
    stop_on_error: bool = typer.Option(
        False, "--stop-on-error", help="遇到失败立即停止（默认继续下一条）"
    ),
) -> None:
    """批量处理多条视频（顺序执行、失败不中断、末尾汇总）。"""
    from .batch import read_sources, run_batch

    try:
        settings = _load(config)
        _setup_logs(settings)
        sources = read_sources(file)
    except VidarError as exc:
        _fail(exc, code=2)
        return

    console.print(f"[bold]批量任务[/bold]：{len(sources)} 条（清单：{file}）")

    def on_event(event: str, data: dict) -> None:
        if event != "batch_item":
            return
        status = str(data.get("status", ""))
        index, total = data.get("index"), data.get("total")
        source = data.get("source")
        if status == "running":
            console.print(f"[cyan]▶ [{index}/{total}] {source}[/cyan]")
        elif status == "done":
            console.print(f"[green]✓ [{index}/{total}] {source}[/green]")
        elif status == "failed":
            console.print(f"[red]✗ [{index}/{total}] {source}：{data.get('error', '')}[/red]")
        elif status == "cancelled":
            console.print(f"[yellow]■ [{index}/{total}] {source} 已取消[/yellow]")

    try:
        result = run_batch(
            settings,
            sources,
            on_event=on_event,
            from_step=from_step,
            to_step=to_step,
            stop_on_error=stop_on_error,
        )
    except KeyboardInterrupt:
        _fail(VidarError("批量任务被中断（已完成的任务可续跑）"), code=130)
        return

    table = Table(title="批量结果")
    table.add_column("来源", style="cyan")
    table.add_column("状态", no_wrap=True)
    table.add_column("说明", style="dim")
    labels = {
        "done": "[green]完成[/green]",
        "failed": "[red]失败[/red]",
        "cancelled": "[yellow]取消[/yellow]",
        "pending": "未执行",
    }
    for item in result.items:
        table.add_row(item.source, labels.get(item.status, item.status), item.error)
    console.print(table)
    summary = f"完成 {result.done_count}/{len(result.items)}"
    if result.failed:
        summary += f"，失败 {len(result.failed)}"
    console.print(f"[bold]{summary}[/bold]")
    if result.failed:
        raise typer.Exit(1)


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
    except VidarError as exc:
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
    except VidarError as exc:
        _fail(exc)
    console.print(f"[green]✓ 已写入配置：{written}[/green]")
    console.print("[dim]编辑后可用 vidar config show 查看生效配置[/dim]")


@config_app.command("show")
def config_show(
    config: Path | None = typer.Option(None, "--config", help="指定配置文件路径"),
    as_json: bool = typer.Option(False, "--json", help="以 JSON 输出"),
) -> None:
    """展示生效配置（密钥掩码）。"""
    try:
        settings = _load(config)
    except VidarError as exc:
        _fail(exc, code=2)
    data = dump_settings(settings)
    console.print(f"[dim]配置文件：{settings.config_path or '（全部默认值）'}[/dim]")
    if as_json:
        console.print_json(json.dumps(data, ensure_ascii=False))
    else:
        import tomli_w

        console.print(tomli_w.dumps(data))


# --------------------------------------------------------------------------- #
# model（ASR 模型辅助下载与校验）
# --------------------------------------------------------------------------- #
@model_app.command("download")
def model_download(
    model: str = typer.Argument("large-v3", help="模型名：large-v3 / medium / small / Systran/xxx"),
    dir: Path | None = typer.Option(None, "--dir", help="目标目录（默认 models/<模型名>）"),
    source: str = typer.Option("hf-mirror", "--source", help="下载源：hf-mirror | huggingface"),
    connections: int = typer.Option(6, "--connections", help="分片并发数"),
) -> None:
    """分片并行下载 ASR 模型（断点续传 + 大小校验）。"""
    from .model_store import ENDPOINTS, default_model_dir, download_model, resolve_repo

    if source not in ENDPOINTS:
        _fail(VidarError(f"未知下载源：{source}", hint="可选：hf-mirror / huggingface"))
    target = dir or default_model_dir(model)
    console.print(f"模型：{resolve_repo(model)}")
    console.print(f"目录：{target}")
    console.print(f"源　：{ENDPOINTS[source]}")

    progress_bar = Progress(
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        DownloadColumn(),
        TransferSpeedColumn(),
        TimeRemainingColumn(),
        console=console,
    )
    with progress_bar:
        task_id = progress_bar.add_task("获取元数据…", total=None)

        def on_progress(name: str, done: int, total: int) -> None:
            progress_bar.update(task_id, description=f"下载 {name}", total=total, completed=done)

        try:
            downloaded, skipped = download_model(
                model, target, source=source, connections=connections, on_progress=on_progress
            )
        except VidarError as exc:
            _fail(exc)
        except KeyboardInterrupt:
            _fail(VidarError("已取消（分片已保留，重跑可续传）"), code=130)

    if skipped:
        console.print(f"[dim]跳过已存在文件：{', '.join(skipped)}[/dim]")
    console.print(f"[green]✓ 下载完成：{len(downloaded)} 个文件[/green]")
    console.print(f"[dim]模型目录：{target}（config.toml 中把 asr.model 指到该目录即可）[/dim]")


@model_app.command("check")
def model_check(
    model: str = typer.Argument("large-v3", help="模型名：large-v3 / medium / small / Systran/xxx"),
    dir: Path | None = typer.Option(None, "--dir", help="模型目录（默认 models/<模型名>）"),
    source: str = typer.Option("hf-mirror", "--source", help="元数据源：hf-mirror | huggingface"),
) -> None:
    """校验模型文件完整性（大小比对，缺失/损坏一目了然）。"""
    from .model_store import ENDPOINTS, check_model, default_model_dir, resolve_repo

    target = dir or default_model_dir(model)
    results = check_model(model, target, endpoint=ENDPOINTS.get(source, source))

    def human(num: int | None) -> str:
        if num is None:
            return "—"
        if num >= 1024**3:
            return f"{num / 1024**3:.2f} GB"
        if num >= 1024**2:
            return f"{num / 1024**2:.1f} MB"
        return f"{num} B"

    table = Table(title=f"模型校验 · {resolve_repo(model)}")
    table.add_column("文件", style="cyan", no_wrap=True)
    table.add_column("期望大小", justify="right")
    table.add_column("实际大小", justify="right")
    table.add_column("结果", no_wrap=True)
    ok_count = 0
    for item in results:
        ok_count += int(item.ok)
        icon = "[green]✓[/green]" if item.ok else "[red]✗[/red]"
        table.add_row(item.name, human(item.expected), human(item.actual), icon)
    console.print(table)
    console.print(f"[dim]目录：{target}[/dim]")
    if ok_count == len(results):
        console.print(f"[green]✓ {ok_count}/{len(results)} 个文件校验通过[/green]")
        return
    console.print(f"[red]✗ {ok_count}/{len(results)} 个文件通过[/red]")
    console.print("[dim]修复：vidar model download（自动重下缺失/损坏文件，支持断点续传）[/dim]")
    raise typer.Exit(1)


@model_app.command("list")
def model_list(
    dir: Path | None = typer.Option(None, "--dir", help="模型根目录（默认 ./models）"),
) -> None:
    """列出常用模型（大小 / 显存建议 / 是否已安装）。"""
    from .model_store import catalog, resolve_repo

    root = dir or Path.cwd() / "models"
    table = Table(title="ASR 模型清单")
    table.add_column("型号", style="cyan", no_wrap=True)
    table.add_column("大小", justify="right")
    table.add_column("显存", justify="right")
    table.add_column("状态", no_wrap=True)
    table.add_column("说明", style="dim")
    for spec in catalog():
        folder = resolve_repo(spec.alias).split("/")[-1]
        installed = (root / folder / "model.bin").exists()
        table.add_row(
            spec.alias,
            f"≈{spec.approx_mb / 1024:.1f} GB",
            f"≥{spec.vram_gb:.0f} GB",
            "[green]已安装[/green]" if installed else "—",
            spec.note,
        )
    console.print(table)
    console.print(
        "[dim]安装：vidar model download <型号>　·　档位推荐：vidar model recommend[/dim]"
    )


@model_app.command("recommend")
def model_recommend() -> None:
    """按显卡显存推荐模型档位（含一键安装命令）。"""
    from .asr import cuda_available
    from .model_store import detect_vram_gb, recommend_model

    vram = detect_vram_gb()
    has_cuda = cuda_available()
    spec, reason = recommend_model(vram, has_cuda=has_cuda)
    console.print(f"[bold]{reason}[/bold]")
    console.print(f"安装命令：vidar model download {spec.alias}")
    if spec.alias != "large-v3":
        console.print("[dim]显存充足且追求精度时可选择 large-v3[/dim]")


# --------------------------------------------------------------------------- #
# llm（接入引导）
# --------------------------------------------------------------------------- #
@llm_app.command("presets")
def llm_presets() -> None:
    """列出内置服务商预设（含 Key 环境变量与获取链接）。"""
    from .llm.providers import CUSTOM_HINT, PRESETS

    table = Table(title="LLM 服务商预设")
    table.add_column("预设", style="cyan", no_wrap=True)
    table.add_column("名称")
    table.add_column("默认模型", no_wrap=True)
    table.add_column("Key 环境变量", no_wrap=True)
    table.add_column("说明", style="dim")
    for preset in PRESETS:
        table.add_row(
            preset.key,
            preset.label,
            preset.model,
            preset.env_key or "（无需）",
            preset.note,
        )
    console.print(table)
    from rich.markup import escape as markup_escape

    console.print(f"[dim]{markup_escape(CUSTOM_HINT)}[/dim]")
    console.print("[dim]应用预设：vidar llm setup <预设> [--key sk-...] --write[/dim]")


@llm_app.command("test")
def llm_test(
    config: Path | None = typer.Option(None, "--config", help="指定配置文件路径"),
    base: str | None = typer.Option(None, "--base", help="临时覆盖 api_base"),
    key: str | None = typer.Option(None, "--key", help="临时覆盖 api_key"),
    model: str | None = typer.Option(None, "--model", help="临时覆盖模型名"),
) -> None:
    """测试 LLM 连通性（1 token 请求，成本可忽略）。"""
    from .llm.providers import detect_preset, test_connection

    try:
        settings = _load(config)
    except VidarError as exc:
        _fail(exc, code=2)
    overrides = {
        name: value
        for name, value in (("api_base", base), ("api_key", key), ("model", model))
        if value
    }
    llm_settings = settings.llm.model_copy(update=overrides) if overrides else settings.llm
    preset = detect_preset(llm_settings.api_base)
    if preset is not None:
        console.print(f"[dim]当前预设：{preset.label}[/dim]")
    report = test_connection(llm_settings)
    if report.ok:
        console.print(f"[green]✓ {report.message}[/green]")
        return
    from rich.markup import escape as markup_escape

    console.print(f"[red]✗ {markup_escape(report.message)}[/red]")
    if report.hint:
        console.print(f"[yellow]建议：{markup_escape(report.hint)}[/yellow]")
    raise typer.Exit(1)


@llm_app.command("setup")
def llm_setup(
    preset: str = typer.Argument(..., help="预设 key（vidar llm presets 查看）"),
    key: str | None = typer.Option(None, "--key", help="API Key（不传则读该预设的环境变量）"),
    model: str | None = typer.Option(None, "--model", help="覆盖默认模型名"),
    config: Path | None = typer.Option(None, "--config", help="目标配置文件（默认当前/用户配置）"),
    write: bool = typer.Option(False, "--write", help="真正写入配置文件（默认只预览）"),
) -> None:
    """应用服务商预设：预览 → --write 落盘 → vidar llm test 验证。"""
    import os

    from .config import (
        deep_merge,
        default_config_path,
        export_settings_dict,
        write_config_dict,
    )
    from .llm.providers import PRESETS, find_preset

    chosen = find_preset(preset)
    if chosen is None:
        _fail(
            VidarError(
                f"未知预设：{preset}",
                hint="可选：" + " / ".join(item.key for item in PRESETS),
            )
        )
    try:
        settings = _load(config)
    except VidarError as exc:
        _fail(exc, code=2)

    env_value = os.environ.get(chosen.env_key, "") if chosen.env_key else ""
    resolved_key = key or env_value
    target = config or settings.config_path or default_config_path()

    console.print(f"预设　：{chosen.label}")
    console.print(f"地址　：{chosen.api_base}")
    console.print(f"模型　：{model or chosen.model}")
    if chosen.env_key:
        if key:
            key_source = "命令行 --key"
        elif env_value:
            key_source = f"环境变量 {chosen.env_key}"
        else:
            key_source = "未找到（可稍后用 --key 或在 GUI 设置中填写）"
        console.print(f"Key　：{key_source}")
    if chosen.key_url:
        console.print(f"[dim]获取 Key：{chosen.key_url}[/dim]")

    patch = {
        "llm": {
            "api_base": chosen.api_base,
            "model": model or chosen.model,
            "api_key": resolved_key,
        }
    }
    if not write:
        console.print(f"[dim]预览模式：加 --write 写入 {target}[/dim]")
        return
    if resolved_key and not key and env_value:
        patch["llm"]["api_key"] = ""  # 环境变量已就绪：不把 Key 写进文件
        console.print("[dim]检测到环境变量已提供 Key，配置文件中的 api_key 留空（更安全）[/dim]")
    data = deep_merge(export_settings_dict(settings), patch)
    write_config_dict(target, data)
    console.print(f"[green]✓ 已写入配置：{target}[/green]")
    console.print("[dim]下一步：vidar llm test[/dim]")


# --------------------------------------------------------------------------- #
# gpu（CUDA 运行时辅助安装）
# --------------------------------------------------------------------------- #
@gpu_app.command("install")
def gpu_install(
    target: Path | None = typer.Option(
        None, "--target", help="安装目录（默认：程序目录 / 当前目录）"
    ),
    shared: bool = typer.Option(
        False, "--shared", help="安装到机器级共享目录（多版本便携包共用一份）"
    ),
    mirror: str = typer.Option("tuna", "--mirror", help="下载源：tuna | aliyun | pypi"),
    full: bool = typer.Option(False, "--full", help="安装全部 DLL（默认最小可用集，已实测）"),
) -> None:
    """从 PyPI 镜像下载 cuBLAS/cuDNN 并安装（N 卡加速，约 1.2GB）。"""
    from .config import shared_data_dir
    from .gpu_pack import MIRRORS, install_gpu_runtime

    if mirror not in MIRRORS:
        _fail(VidarError(f"未知下载源：{mirror}", hint="可选：tuna / aliyun / pypi"))
    base_dir = target or (shared_data_dir() if shared else None) or exe_dir() or Path.cwd()
    console.print(f"安装目录：{Path(base_dir) / 'nvidia'}")
    console.print(f"下载源　：{MIRRORS[mirror]}")

    progress_bar = Progress(
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        DownloadColumn(),
        TransferSpeedColumn(),
        TimeRemainingColumn(),
        console=console,
    )
    with progress_bar:
        task_id = progress_bar.add_task("获取元数据…", total=None)

        def on_progress(name: str, done: int, total: int) -> None:
            progress_bar.update(task_id, description=name, total=total, completed=done)

        try:
            installed = install_gpu_runtime(
                Path(base_dir), mirror=mirror, full=full, on_progress=on_progress
            )
        except VidarError as exc:
            _fail(exc)
        except KeyboardInterrupt:
            _fail(VidarError("已取消（已安装部分保留，重跑可继续）"), code=130)

    console.print(f"[green]✓ 已安装 {len(installed)} 个 DLL[/green]")
    console.print("[dim]验证：vidar doctor（faster-whisper 行应显示 CUDA 设备 1）[/dim]")


# --------------------------------------------------------------------------- #
# version / gui
# --------------------------------------------------------------------------- #
@app.command()
def version() -> None:
    """显示版本号。"""
    console.print(f"VIDAR v{__version__}")


@app.command()
def gui(
    config: Path | None = typer.Option(None, "--config", help="指定配置文件路径"),
) -> None:
    """启动图形界面（需要 uv sync --extra gui）。"""
    try:
        settings = _load(config)
    except VidarError as exc:
        _fail(exc, code=2)
    try:
        from .gui.app import run_gui
    except ImportError:
        _fail(
            VidarError(
                "未安装 GUI 依赖 PySide6",
                hint="执行 uv sync --extra gui 安装后重试",
            ),
            code=2,
        )
    run_gui(settings)


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        with contextlib.suppress(Exception):  # 个别终端不支持，忽略
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    app()


if __name__ == "__main__":
    main()

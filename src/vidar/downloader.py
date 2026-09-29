"""yt-dlp 下载封装：仅下载音频流（省带宽、省磁盘、省时间）。"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from .errors import CancelledError, DependencyError, NetworkError
from .source import normalize_source

if TYPE_CHECKING:
    from .pipeline.context import RunContext


def download_audio(ctx: RunContext) -> Path:
    """下载最佳音频流到 work/<key>/source.<ext>，返回文件路径。"""
    try:
        import yt_dlp
    except ImportError as exc:  # pragma: no cover
        raise DependencyError("缺少依赖 yt-dlp", hint="先执行 uv sync") from exc

    work = ctx.work_dir
    for old in work.glob("source.*"):  # 清理旧文件，避免残留混淆
        old.unlink(missing_ok=True)

    def hook(payload: dict) -> None:
        if ctx.cancel_event.is_set():
            raise CancelledError()
        if payload.get("status") == "downloading":
            total = payload.get("total_bytes") or payload.get("total_bytes_estimate") or 0
            downloaded = payload.get("downloaded_bytes") or 0
            if total:
                ctx.emit(
                    "progress",
                    step="download",
                    done=downloaded,
                    total=total,
                    message=f"已下载 {downloaded / 1048576:.1f} / {total / 1048576:.1f} MB",
                )

    options: dict = {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,  # 用我们自己的进度事件，屏蔽 yt-dlp 原生进度条
        "noplaylist": True,
        "format": "bestaudio/best",
        "outtmpl": str(work / "source.%(ext)s"),
        "progress_hooks": [hook],
        "socket_timeout": 30,
        "retries": 3,
    }
    cookies = ctx.settings.paths.cookies
    if cookies and Path(cookies).exists():
        options["cookiefile"] = str(cookies)

    url = normalize_source(ctx.source)
    ctx.emit("progress", step="download", done=0, total=1, message="连接 B 站…")
    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            ydl.download([url])
    except CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 - yt-dlp 异常类型不稳定
        if ctx.cancel_event.is_set():
            raise CancelledError() from exc
        raise NetworkError(
            f"下载失败：{exc}",
            hint="检查网络；需要登录态时在 config.toml 配置 paths.cookies",
        ) from exc

    files = sorted(work.glob("source.*"))
    if not files:
        raise NetworkError("下载结束但未找到音频文件")
    path = files[0]
    ctx.log.info("下载完成：%s（%.1f MB）", path.name, path.stat().st_size / 1048576)
    return path

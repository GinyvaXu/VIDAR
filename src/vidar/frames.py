"""关键帧抽帧：720p 视频下载 → 按 LLM 锚点抽帧 → 去重 → 落盘。

产物（work/<key>/）：
    images/cover.jpg     B 站封面（可选）
    images/fig01.jpg …   关键帧（含 frames.json 元数据）
    frames.json          [{index, start, caption, file}]
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from .errors import CancelledError, DependencyError, NetworkError, VidarError
from .models import SummaryResult, VideoMeta

if TYPE_CHECKING:
    from .pipeline.context import RunContext

COVER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


@dataclass
class ExtractedFrame:
    index: int
    start: float
    caption: str
    filename: str


def download_video(ctx: RunContext, url: str, max_height: int, stem: Path) -> Path:
    """下载 ≤max_height 的视频流（仅视频轨，无音频）。"""
    try:
        import yt_dlp
    except ImportError as exc:  # pragma: no cover
        raise DependencyError("缺少依赖 yt-dlp", hint="先执行 uv sync") from exc

    for old in stem.parent.glob(stem.name + ".*"):
        old.unlink(missing_ok=True)

    def hook(payload: dict) -> None:
        if ctx.cancel_event.is_set():
            raise CancelledError()
        if payload.get("status") == "downloading":
            total = payload.get("total_bytes") or payload.get("total_bytes_estimate") or 0
            done = payload.get("downloaded_bytes") or 0
            if total:
                ctx.emit(
                    "progress",
                    step="frames",
                    done=done,
                    total=total,
                    message=(
                        f"下载 {max_height}p 视频 "
                        f"{done / 1048576:.1f}/{total / 1048576:.1f} MB"
                    ),
                )

    options: dict = {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "noplaylist": True,
        "format": (
            f"bestvideo[height<={max_height}][ext=mp4]"
            f"/bestvideo[height<={max_height}]/best[height<={max_height}]"
        ),
        "outtmpl": f"{stem}.%(ext)s",
        "progress_hooks": [hook],
        "socket_timeout": 30,
        "retries": 3,
    }
    cookies = ctx.settings.paths.cookies
    if cookies and Path(cookies).exists():
        options["cookiefile"] = str(cookies)

    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            ydl.download([url])
    except CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 - yt-dlp 异常类型不稳定
        if ctx.cancel_event.is_set():
            raise CancelledError() from exc
        raise NetworkError(
            f"低清视频下载失败：{exc}",
            hint="可关闭 export.screenshots 后重跑，或稍后重试",
        ) from exc

    candidates = [
        item
        for item in sorted(stem.parent.glob(stem.name + ".*"))
        if item.is_file() and item.suffix not in (".json", ".part")
    ]
    if not candidates:
        raise NetworkError("下载结束但未找到视频文件")
    return candidates[0]


def _download_cover(ctx: RunContext, meta: VideoMeta, dest: Path) -> bool:
    if not meta.thumbnail_url:
        return False
    try:
        import httpx

        headers = {"User-Agent": COVER_UA, "Referer": "https://www.bilibili.com/"}
        with httpx.Client(timeout=30, follow_redirects=True, headers=headers) as client:
            response = client.get(meta.thumbnail_url)
            response.raise_for_status()
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(response.content)
        return True
    except Exception as exc:  # noqa: BLE001 - 封面缺失不影响主流程
        ctx.log.warning("封面下载失败：%s", exc)
        return False


def _extract_frame(video: Path, timestamp: float, output: Path) -> object | None:
    """在指定时刻抽一帧，返回 PIL.Image（失败为 None）。"""
    import av

    with av.open(str(video)) as container:
        if not container.streams.video:
            raise VidarError("该视频不含视频流，无法抽帧")
        stream = container.streams.video[0]
        container.seek(int(max(0.0, timestamp - 3.0) * av.time_base), backward=True)
        best = None
        for frame in container.decode(stream):
            best = frame
            if frame.time is not None and frame.time >= timestamp:
                break
        if best is None:
            return None
        return best.to_image()


def _dhash(image: object) -> int:
    small = image.convert("L").resize((9, 8))  # type: ignore[attr-defined]
    pixels = list(small.getdata())  # type: ignore[attr-defined]
    bits = 0
    for row in range(8):
        for col in range(8):
            left = pixels[row * 9 + col]
            right = pixels[row * 9 + col + 1]
            if left > right:
                bits |= 1 << (row * 8 + col)
    return bits


def _hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def extract_figures(
    ctx: RunContext,
    meta: VideoMeta,
    summary: SummaryResult,
    *,
    max_height: int,
    min_gap: float,
) -> list[ExtractedFrame]:
    """按 summary 的配图锚点抽帧（含封面），返回全部产物。"""
    images_dir = ctx.artifact("images")
    images_dir.mkdir(parents=True, exist_ok=True)
    ctx.emit("progress", step="frames", done=0, total=1, message="准备封面…")
    _download_cover(ctx, meta, images_dir / "cover.jpg")

    # 收集全部锚点（按时间排序）
    points: list[tuple[float, str]] = []
    for chapter in summary.chapters:
        for figure in chapter.figures:
            points.append((figure.start, figure.caption))
    points.sort(key=lambda item: item[0])
    if not points:
        ctx.log.info("LLM 判断本视频无需配图（figures 为空）")
        _write_frames_json(ctx, [])
        return []

    if not meta.url:
        raise VidarError("缺少视频链接，无法下载视频抽帧")

    stem = ctx.artifact("video-tmp")
    video = download_video(ctx, meta.url, max_height, stem)
    ctx.log.info("低清视频就绪：%s（%.1f MB）", video.name, video.stat().st_size / 1048576)

    frames: list[ExtractedFrame] = []
    hashes: list[int] = []
    last_time = -1e9
    total = len(points)
    try:
        for position, (start, caption) in enumerate(points, start=1):
            ctx.raise_if_cancelled()
            if start - last_time < min_gap:
                ctx.log.info("跳过过近的配图（%.0fs 间隔不足）", start - last_time)
                continue
            ctx.emit(
                "progress",
                step="frames",
                done=position - 1,
                total=total,
                message=f"抽帧 {position}/{total}（{int(start) // 60:02d}:{int(start) % 60:02d}）",
            )
            image = _extract_frame(video, start, ctx.artifact("frame.jpg"))
            if image is None:
                ctx.log.warning("第 %d 个锚点抽帧失败（%.1fs）", position, start)
                continue
            fingerprint = _dhash(image)
            if any(_hamming(fingerprint, other) <= 6 for other in hashes):
                ctx.log.info("跳过重复画面（%.1fs）", start)
                continue
            hashes.append(fingerprint)
            filename = f"fig{len(frames) + 1:02d}.jpg"
            target = images_dir / filename
            image.convert("RGB").save(target, "JPEG", quality=85, optimize=True)  # type: ignore[attr-defined]
            frames.append(
                ExtractedFrame(
                    index=len(frames) + 1, start=start, caption=caption, filename=filename
                )
            )
            last_time = start
    finally:
        if not ctx.settings.export.keep_media:
            video.unlink(missing_ok=True)
            ctx.log.info("已清理低清视频文件")

    ctx.emit("progress", step="frames", done=1, total=1, message=f"完成，共 {len(frames)} 张配图")
    _write_frames_json(ctx, frames)
    return frames


def _write_frames_json(ctx: RunContext, frames: list[ExtractedFrame]) -> None:
    payload = [
        {"index": f.index, "start": f.start, "caption": f.caption, "file": f.filename}
        for f in frames
    ]
    ctx.artifact("frames.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def load_frames(ctx: RunContext) -> list[ExtractedFrame]:
    path = ctx.artifact("frames.json")
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return []
    return [
        ExtractedFrame(
            index=int(item.get("index", 0)),
            start=float(item.get("start", 0.0)),
            caption=str(item.get("caption") or ""),
            filename=str(item.get("file") or ""),
        )
        for item in payload
        if isinstance(item, dict) and item.get("file")
    ]

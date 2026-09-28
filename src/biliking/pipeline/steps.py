"""8 个流水线步骤。

M0 状态：resolve 已实现；download/audio/asr 属 M1；refine/chapters/summarize/export 属 M2。
未实现的步骤抛出 MilestoneError，runner 会给出明确提示并记录状态。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from ..errors import BilikingError, DependencyError, MilestoneError, NetworkError
from ..models import VideoMeta
from ..utils.text import parse_bvid
from .context import RunContext


def normalize_source(source: str) -> str:
    """BV 号 → 完整 URL；校验 URL 合法性。"""
    source = source.strip()
    bvid = parse_bvid(source)
    if bvid and "http" not in source.lower():
        return f"https://www.bilibili.com/video/{bvid}"
    if not source.lower().startswith("http"):
        raise BilikingError(
            f"无法识别的视频地址：{source}",
            hint="请输入形如 BV1xxxxxxxxx 的 BV 号，或完整视频 URL",
        )
    return source


def _extract_page(url: str) -> int:
    try:
        query = parse_qs(urlparse(url).query)
        return int(query.get("p", ["1"])[0])
    except (ValueError, TypeError):
        return 1


class ResolveStep:
    """解析视频元信息（M1 范围，提前实现）。"""

    name = "resolve"
    title = "解析视频信息"
    milestone: str | None = None

    def run(self, ctx: RunContext) -> dict[str, str]:
        url = normalize_source(ctx.source)
        try:
            import yt_dlp
        except ImportError as exc:  # pragma: no cover
            raise DependencyError("缺少依赖 yt-dlp", hint="先执行 uv sync") from exc

        options: dict = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "noplaylist": True,
            "socket_timeout": 30,
        }
        cookies = ctx.settings.paths.cookies
        if cookies:
            cookie_path = Path(cookies)
            if not cookie_path.exists():
                raise BilikingError(
                    f"cookies 文件不存在：{cookie_path}",
                    hint="检查配置 paths.cookies，或删除该配置",
                )
            options["cookiefile"] = str(cookie_path)

        try:
            with yt_dlp.YoutubeDL(options) as ydl:
                info = ydl.extract_info(url, download=False)
        except Exception as exc:  # yt-dlp 异常类型不稳定，统一包装
            raise NetworkError(
                f"解析视频失败：{exc}",
                hint="检查网络/链接有效性；需要登录态时配置 cookies.txt",
            ) from exc
        if not isinstance(info, dict):
            raise NetworkError("解析结果为空，视频可能不存在或需要登录")

        bvid = str(info.get("id") or "")
        if not bvid.startswith("BV"):
            bvid = parse_bvid(str(info.get("webpage_url") or url)) or ctx.key

        up_mid: int | None = None
        try:
            up_mid = int(info["uploader_id"]) if info.get("uploader_id") is not None else None
        except (ValueError, TypeError):
            up_mid = None

        timestamp = info.get("timestamp")
        pubdate = datetime.fromtimestamp(timestamp) if isinstance(timestamp, (int, float)) else None

        meta = VideoMeta(
            bvid=bvid,
            cid=int(info["cid"]) if info.get("cid") is not None else None,
            page=_extract_page(url),
            title=str(info.get("title") or bvid),
            up_name=str(info.get("uploader") or info.get("channel") or ""),
            up_mid=up_mid,
            url=str(info.get("webpage_url") or url),
            pubdate=pubdate,
            duration_sec=(
                float(info["duration"]) if isinstance(info.get("duration"), (int, float)) else None
            ),
            description=str(info.get("description") or ""),
            tags=[str(t) for t in (info.get("tags") or [])],
        )
        ctx.save_meta(meta)
        ctx.log.info("解析成功：%s（%s，时长 %s）", meta.title, meta.up_name, meta.duration_sec)
        return {"meta": "meta.json"}


class DownloadStep:
    name = "download"
    title = "下载音频流（yt-dlp，仅音频）"
    milestone = "M1"

    def run(self, ctx: RunContext) -> dict[str, str]:
        raise MilestoneError(self.milestone, self.title)


class AudioStep:
    name = "audio"
    title = "音频转码（16kHz 单声道 WAV）"
    milestone = "M1"

    def run(self, ctx: RunContext) -> dict[str, str]:
        raise MilestoneError(self.milestone, self.title)


class AsrStep:
    name = "asr"
    title = "语音识别（faster-whisper）"
    milestone = "M1"

    def run(self, ctx: RunContext) -> dict[str, str]:
        raise MilestoneError(self.milestone, self.title)


class RefineStep:
    name = "refine"
    title = "文稿精修（LLM 段级）"
    milestone = "M2"

    def run(self, ctx: RunContext) -> dict[str, str]:
        raise MilestoneError(self.milestone, self.title)


class ChaptersStep:
    name = "chapters"
    title = "章节切分（LLM）"
    milestone = "M2"

    def run(self, ctx: RunContext) -> dict[str, str]:
        raise MilestoneError(self.milestone, self.title)


class SummarizeStep:
    name = "summarize"
    title = "摘要提炼（map-reduce）"
    milestone = "M2"

    def run(self, ctx: RunContext) -> dict[str, str]:
        raise MilestoneError(self.milestone, self.title)


class ExportStep:
    name = "export"
    title = "导出 Markdown 知识文档"
    milestone = "M2"

    def run(self, ctx: RunContext) -> dict[str, str]:
        raise MilestoneError(self.milestone, self.title)


def default_steps() -> list[object]:
    return [
        ResolveStep(),
        DownloadStep(),
        AudioStep(),
        AsrStep(),
        RefineStep(),
        ChaptersStep(),
        SummarizeStep(),
        ExportStep(),
    ]


STEP_ORDER = [
    "resolve",
    "download",
    "audio",
    "asr",
    "refine",
    "chapters",
    "summarize",
    "export",
]

"""8 个流水线步骤。

M1 已实现：resolve / download / audio / asr（产出 asr.json 与原始逐字稿）。
M2 待实现：refine / chapters / summarize / export。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel

from ..asr import transcribe
from ..chapters import build_chapters
from ..downloader import download_audio
from ..errors import DependencyError, NetworkError, VidarError
from ..export import export_outputs
from ..models import AsrResult, Chapter, RefineResult, SummaryResult, VideoMeta
from ..refine import refine_asr_result
from ..render import render_asr_transcript, render_refined_transcript
from ..source import extract_page, normalize_source
from ..summarize import summarize
from ..utils.ffmpeg import extract_audio, find_ffmpeg
from ..utils.text import format_clock, parse_bvid
from .context import RunContext

ModelT = TypeVar("ModelT", bound=BaseModel)


def _load_json_model(ctx: RunContext, filename: str, model_type: type[ModelT]) -> ModelT:
    path = ctx.artifact(filename)
    if not path.exists():
        raise VidarError(
            f"缺少中间产物 {filename}",
            hint="请先运行前置步骤：resolve → download → audio → asr → refine → "
            "chapters → summarize",
        )
    return model_type.model_validate_json(path.read_text(encoding="utf-8"))


def _load_chapters(ctx: RunContext) -> list[Chapter]:
    path = ctx.artifact("chapters.json")
    if not path.exists():
        raise VidarError(
            "缺少中间产物 chapters.json",
            hint="请先运行前置步骤（… → refine → chapters）",
        )
    return [Chapter.model_validate(item) for item in json.loads(path.read_text(encoding="utf-8"))]


class ResolveStep:
    """解析视频元信息（通过 yt-dlp，无需下载）。"""

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
                raise VidarError(
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
            page=extract_page(url),
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
    """只下载最佳音频流，不下载视频。"""

    name = "download"
    title = "下载音频流（yt-dlp，仅音频）"
    milestone: str | None = None

    def run(self, ctx: RunContext) -> dict[str, str]:
        path = download_audio(ctx)
        return {"audio": path.name}


class AudioStep:
    """转码为 16kHz 单声道 WAV（ASR 标准输入）。"""

    name = "audio"
    title = "音频转码（16kHz 单声道 WAV）"
    milestone: str | None = None

    def run(self, ctx: RunContext) -> dict[str, str]:
        ffmpeg = find_ffmpeg(ctx.settings.paths)
        if not ffmpeg:
            raise DependencyError(
                "未找到 ffmpeg",
                hint="uv sync --extra asr 会附带静态 ffmpeg；也可在 config.toml 配置 paths.ffmpeg",
            )
        source_name = ctx.state.record("download").artifacts.get("audio")
        if not source_name or not (ctx.work_dir / source_name).exists():
            raise VidarError("未找到下载的音频文件", hint="请先运行 download 步骤")

        wav = ctx.artifact("audio.wav")
        ctx.emit("progress", step="audio", done=0, total=1, message="转码中…")
        extract_audio(
            ffmpeg,
            ctx.work_dir / source_name,
            wav,
            should_cancel=ctx.cancel_event.is_set,
        )
        ctx.emit("progress", step="audio", done=1, total=1, message="转码完成")
        return {"wav": "audio.wav"}


class AsrStep:
    """faster-whisper 语音识别 → asr.json + 原始逐字稿。"""

    name = "asr"
    title = "语音识别（faster-whisper）"
    milestone: str | None = None

    def run(self, ctx: RunContext) -> dict[str, str]:
        wav_name = ctx.state.record("audio").artifacts.get("wav") or "audio.wav"
        wav = ctx.work_dir / wav_name
        if not wav.exists():
            raise VidarError("未找到 WAV 音频文件", hint="请先运行 audio 步骤")

        result = transcribe(ctx, wav)
        ctx.artifact("asr.json").write_text(result.model_dump_json(indent=2), encoding="utf-8")
        ctx.artifact("transcript.raw.md").write_text(
            render_asr_transcript(result), encoding="utf-8"
        )
        ctx.log.info(
            "识别完成：%d 段，时长 %s",
            len(result.segments),
            format_clock(result.duration_sec or 0),
        )
        return {"asr": "asr.json", "raw": "transcript.raw.md"}


class RefineStep:
    name = "refine"
    title = "文稿精修（LLM 段级）"
    milestone: str | None = None

    def run(self, ctx: RunContext) -> dict[str, str]:
        asr = _load_json_model(ctx, "asr.json", AsrResult)
        result = refine_asr_result(ctx, asr)
        ctx.artifact("refine.json").write_text(result.model_dump_json(indent=2), encoding="utf-8")
        ctx.artifact("transcript.refined.md").write_text(
            render_refined_transcript(result), encoding="utf-8"
        )
        ctx.log.info(
            "精修完成：%d 段，tokens %d/%d",
            len(result.segments),
            result.tokens_in,
            result.tokens_out,
        )
        return {"refine": "refine.json", "refined": "transcript.refined.md"}


class ChaptersStep:
    name = "chapters"
    title = "章节切分（LLM）"
    milestone: str | None = None

    def run(self, ctx: RunContext) -> dict[str, str]:
        refine = _load_json_model(ctx, "refine.json", RefineResult)
        chapters, _client = build_chapters(ctx, refine)
        payload = json.dumps([ch.model_dump() for ch in chapters], ensure_ascii=False, indent=2)
        ctx.artifact("chapters.json").write_text(payload, encoding="utf-8")
        ctx.log.info("章节切分完成：%d 章", len(chapters))
        return {"chapters": "chapters.json"}


class SummarizeStep:
    name = "summarize"
    title = "摘要提炼（map-reduce）"
    milestone: str | None = None

    def run(self, ctx: RunContext) -> dict[str, str]:
        refine = _load_json_model(ctx, "refine.json", RefineResult)
        chapters = _load_chapters(ctx)
        summary, _client = summarize(ctx, refine, chapters)
        ctx.artifact("summary.json").write_text(summary.model_dump_json(indent=2), encoding="utf-8")
        ctx.log.info(
            "摘要完成：%d 条要点，%d 章小结",
            len(summary.key_points),
            len(summary.chapters),
        )
        return {"summary": "summary.json"}


class ExportStep:
    name = "export"
    title = "导出 Markdown 知识文档"
    milestone: str | None = None

    def run(self, ctx: RunContext) -> dict[str, str]:
        meta = ctx.meta or _load_json_model(ctx, "meta.json", VideoMeta)
        asr = _load_json_model(ctx, "asr.json", AsrResult)
        refine = _load_json_model(ctx, "refine.json", RefineResult)
        chapters = _load_chapters(ctx)
        summary = _load_json_model(ctx, "summary.json", SummaryResult)
        return export_outputs(ctx, meta, asr, refine, chapters, summary)


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

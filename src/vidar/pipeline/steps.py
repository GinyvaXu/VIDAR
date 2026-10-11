"""12 个流水线步骤：resolve → download → audio → asr → refine → ads → translate
→ chapters → summarize → paragraphs → frames → export。"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel

from ..ads import ads_range_key, detect_ads, load_ads, load_refine_effective
from ..asr import transcribe
from ..chapters import build_chapters
from ..downloader import download_audio
from ..errors import DependencyError, NetworkError, VidarError
from ..export import export_outputs
from ..frames import extract_figures
from ..models import AsrResult, Chapter, RefineResult, SummaryResult, VideoMeta
from ..paragraphs import build_paragraphs
from ..refine import refine_asr_result
from ..render import render_asr_transcript, render_refined_transcript
from ..source import extract_page, normalize_source
from ..summarize import summarize
from ..translate import apply_translation, load_translation, should_translate, translate_segments
from ..translate import language_name as language_display_name
from ..utils.audio import decode_to_wav
from ..utils.text import format_clock, parse_bvid
from .context import RunContext

ModelT = TypeVar("ModelT", bound=BaseModel)


def _load_json_model(ctx: RunContext, filename: str, model_type: type[ModelT]) -> ModelT:
    path = ctx.artifact(filename)
    if not path.exists():
        raise VidarError(
            f"缺少中间产物 {filename}",
            hint="请先运行前置步骤：resolve → download → audio → asr → refine → "
            "ads → translate → chapters → summarize",
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
            thumbnail_url=str(info.get("thumbnail") or ""),
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
    """解码为 16kHz 单声道 WAV（ASR 标准输入；基于 PyAV，无需外部 ffmpeg）。"""

    name = "audio"
    title = "音频解码（16kHz 单声道 WAV）"
    milestone: str | None = None

    def run(self, ctx: RunContext) -> dict[str, str]:
        source_name = ctx.state.record("download").artifacts.get("audio")
        if not source_name or not (ctx.work_dir / source_name).exists():
            raise VidarError("未找到下载的音频文件", hint="请先运行 download 步骤")

        wav = ctx.artifact("audio.wav")
        ctx.emit("progress", step="audio", done=0, total=1, message="解码音频中…")
        decode_to_wav(ctx.work_dir / source_name, wav, should_cancel=ctx.cancel_event.is_set)
        ctx.emit("progress", step="audio", done=1, total=1, message="解码完成")
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


class AdsStep:
    """广告识别（LLM 段级）：refine.json → ads.json（被标记的段号区间）。

    下游（翻译/章节/摘要/语段/导出/重渲染）统一经「有效视图」消费精修稿，
    自动省略广告段；被省略内容由导出层另存「省略广告版-*.txt」记录。
    """

    name = "ads"
    title = "广告识别（LLM）"
    milestone: str | None = None

    def run(self, ctx: RunContext) -> dict[str, str]:
        stale = ctx.artifact("ads.json")
        if not ctx.settings.ads.enabled:
            ctx.log.info("广告识别已关闭（ads.enabled=false），跳过")
            stale.unlink(missing_ok=True)
            return {}

        previous = load_ads(ctx)
        refine = _load_json_model(ctx, "refine.json", RefineResult)
        result = detect_ads(ctx, refine)
        stale.write_text(result.model_dump_json(indent=2), encoding="utf-8")

        if result.ranges:
            seconds = sum(item.duration_sec for item in result.ranges)
            ctx.log.info(
                "广告识别完成：%d 段，合计 %s（成品将省略）",
                len(result.ranges),
                format_clock(seconds),
            )
            if len(refine.segments) <= len(
                {idx for r in result.ranges for idx in range(r.start_idx, r.end_idx + 1)}
            ):
                ctx.log.warning("全部段落都被识别为广告——如为误判可在配置关闭 [ads] 后重跑")
        else:
            ctx.log.info("广告识别完成：未发现广告段落")

        # 广告区间变化时清理下游按块检查点（段号重编号后旧译文/旧摘要不可复用）
        if ads_range_key(previous) != ads_range_key(result):
            for name in ("translate.partial.json", "summarize.partial.json"):
                ctx.artifact(name).unlink(missing_ok=True)
        return {"ads": "ads.json"}


class TranslateStep:
    """双语翻译（LLM）：精修稿 → 目标语言译文（1:1 段号，供双语对照）。"""

    name = "translate"
    title = "双语翻译（LLM）"
    milestone: str | None = None

    def run(self, ctx: RunContext) -> dict[str, str]:
        settings = ctx.settings.translate
        stale = ctx.artifact("translate.json")
        if not settings.enabled:
            ctx.log.info("翻译已关闭（translate.enabled=false），跳过")
            stale.unlink(missing_ok=True)
            return {}

        refine = load_refine_effective(ctx)
        asr = _load_json_model(ctx, "asr.json", AsrResult)
        if not should_translate(
            asr.language, settings.target_language, skip_same=settings.skip_languages
        ):
            ctx.log.info(
                "源语言（%s）即目标语言（%s），跳过翻译",
                language_display_name(asr.language),
                language_display_name(settings.target_language),
            )
            stale.unlink(missing_ok=True)
            return {}

        result = translate_segments(ctx, refine, asr.language)
        ctx.artifact("translate.json").write_text(
            result.model_dump_json(indent=2), encoding="utf-8"
        )
        ctx.log.info(
            "翻译完成：%d 段（%s → %s，tokens %d/%d）",
            len(result.segments),
            result.source_language,
            result.target_language,
            result.tokens_in,
            result.tokens_out,
        )
        return {"translate": "translate.json"}


class ChaptersStep:
    name = "chapters"
    title = "章节切分（LLM）"
    milestone: str | None = None

    def run(self, ctx: RunContext) -> dict[str, str]:
        refine = load_refine_effective(ctx)
        # 有译文时章节基于译文切分（文档语言与目标语言一致）
        refine = apply_translation(refine, load_translation(ctx))
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
        refine = load_refine_effective(ctx)
        refine = apply_translation(refine, load_translation(ctx))
        chapters = _load_chapters(ctx)
        summary, _client = summarize(ctx, refine, chapters)
        ctx.artifact("summary.json").write_text(summary.model_dump_json(indent=2), encoding="utf-8")
        ctx.log.info(
            "摘要完成：%d 条要点，%d 章小结",
            len(summary.key_points),
            len(summary.chapters),
        )
        return {"summary": "summary.json"}


class ParagraphsStep:
    """语段编辑（LLM）：把零散句子编成自然段（只决定边界，不改写文字）。"""

    name = "paragraphs"
    title = "语段编辑（LLM）"
    milestone: str | None = None

    def run(self, ctx: RunContext) -> dict[str, str]:
        stale = ctx.artifact("paragraphs.json")
        if not ctx.settings.paragraph.enabled:
            ctx.log.info("语段编辑已关闭（paragraph.enabled=false），跳过")
            stale.unlink(missing_ok=True)
            return {}
        refine = load_refine_effective(ctx)
        translation = load_translation(ctx)
        result = build_paragraphs(ctx, refine, translation)
        stale.write_text(result.model_dump_json(indent=2), encoding="utf-8")
        ctx.log.info("语段编辑完成：%d 段", len(result.paragraphs))
        return {"paragraphs": "paragraphs.json"}


class FramesStep:
    """关键帧截图：下载低清视频，按 LLM 锚点抽帧（供 Word/PDF 配图）。"""

    name = "frames"
    title = "关键帧截图（抽帧）"
    milestone: str | None = None

    def run(self, ctx: RunContext) -> dict[str, str]:
        if not ctx.settings.export.screenshots:
            ctx.log.info("截图已关闭（export.screenshots=false），跳过抽帧")
            ctx.artifact("images").mkdir(parents=True, exist_ok=True)
            ctx.artifact("frames.json").write_text("[]", encoding="utf-8")
            return {"frames": "frames.json", "images": "images"}

        summary = _load_json_model(ctx, "summary.json", SummaryResult)
        meta = ctx.meta or _load_json_model(ctx, "meta.json", VideoMeta)
        frames = extract_figures(
            ctx,
            meta,
            summary,
            max_height=ctx.settings.export.video_max_height,
            min_gap=ctx.settings.export.min_gap_sec,
            extract_points=ctx.settings.export.point_figures != "off",
        )
        ctx.log.info("抽帧完成：%d 张配图", len(frames))
        return {"frames": "frames.json", "images": "images"}


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
        AdsStep(),
        TranslateStep(),
        ChaptersStep(),
        SummarizeStep(),
        ParagraphsStep(),
        FramesStep(),
        ExportStep(),
    ]


STEP_ORDER = [
    "resolve",
    "download",
    "audio",
    "asr",
    "refine",
    "ads",
    "translate",
    "chapters",
    "summarize",
    "paragraphs",
    "frames",
    "export",
]

"""导出：notes.md / outline.md / 逐字稿 / meta.json / SRT，并清理媒体文件。"""

from __future__ import annotations

import json
from datetime import datetime
from typing import TYPE_CHECKING

from .models import AsrResult, Chapter, RefineResult, SummaryResult, VideoMeta
from .render import (
    render_asr_transcript,
    render_notes,
    render_outline,
    render_refined_transcript,
    render_srt,
)

if TYPE_CHECKING:
    from .pipeline.context import RunContext


def build_export_meta(
    meta: VideoMeta,
    asr: AsrResult,
    refine: RefineResult,
    summary: SummaryResult,
) -> dict:
    return {
        "video": meta.model_dump(mode="json"),
        "processing": {
            "asr": {
                "engine": asr.engine,
                "model": asr.model,
                "segments": len(asr.segments),
                "duration_sec": asr.duration_sec,
            },
            "refine": {
                "model": refine.model,
                "segments": len(refine.segments),
                "tokens_in": refine.tokens_in,
                "tokens_out": refine.tokens_out,
            },
            "summary": {
                "model": summary.model,
                "key_points": len(summary.key_points),
                "chapters": len(summary.chapters),
                "tokens_in": summary.tokens_in,
                "tokens_out": summary.tokens_out,
            },
            "exported_at": datetime.now().isoformat(timespec="seconds"),
        },
    }


def cleanup_media(ctx: RunContext) -> list[str]:
    """按配置清理中间媒体：默认全删；keep_audio 留音频；keep_media 全留。"""
    export_cfg = ctx.settings.export
    if export_cfg.keep_media:
        return []
    removed: list[str] = []
    for pattern in ("source.*",):
        for path in ctx.work_dir.glob(pattern):
            path.unlink(missing_ok=True)
            removed.append(path.name)
    wav = ctx.artifact("audio.wav")
    if wav.exists() and not export_cfg.keep_audio:
        wav.unlink(missing_ok=True)
        removed.append("audio.wav")
    return removed


def export_outputs(
    ctx: RunContext,
    meta: VideoMeta,
    asr: AsrResult,
    refine: RefineResult,
    chapters: list[Chapter],
    summary: SummaryResult,
) -> dict[str, str]:
    published = meta.pubdate.strftime("%Y-%m-%d") if meta.pubdate else None
    out_dir = ctx.final_output_dir(meta.title, published)
    out_dir.mkdir(parents=True, exist_ok=True)

    notes_path = out_dir / "notes.md"
    outline_path = out_dir / "outline.md"
    raw_path = out_dir / "transcript.raw.md"
    refined_path = out_dir / "transcript.refined.md"
    meta_path = out_dir / "meta.json"

    asr_label = f"{asr.engine} / {asr.model}"
    notes_path.write_text(render_notes(meta, summary, asr_model=asr_label), encoding="utf-8")
    outline_path.write_text(render_outline(meta, summary), encoding="utf-8")
    raw_path.write_text(render_asr_transcript(asr), encoding="utf-8")
    refined_path.write_text(render_refined_transcript(refine), encoding="utf-8")
    meta_path.write_text(
        json.dumps(build_export_meta(meta, asr, refine, summary), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    artifacts = {
        "notes": str(notes_path.resolve()),
        "outline": str(outline_path.resolve()),
        "output_dir": str(out_dir.resolve()),
    }

    if ctx.settings.export.srt:
        srt_path = out_dir / "transcript.srt"
        srt_path.write_text(render_srt(refine, asr), encoding="utf-8")
        artifacts["srt"] = str(srt_path.resolve())

    removed = cleanup_media(ctx)
    if removed:
        ctx.log.info("已清理媒体文件：%s", "、".join(removed))
    ctx.log.info("导出完成：%s", out_dir)
    return artifacts

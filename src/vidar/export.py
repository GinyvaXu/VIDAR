"""导出：Markdown / 逐字稿 / meta / SRT / Word / PDF / EPUB，并清理媒体文件。

文件名规则：`<变体>版-<视频标题>.<ext>`（如「报刊版-标题.pdf」「论文版-标题.pdf」）。
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime
from typing import TYPE_CHECKING

from .ads import ads_note_text, apply_ads, apply_ads_to_asr, load_ads
from .models import AdsResult, AsrResult, Chapter, RefineResult, SummaryResult, VideoMeta
from .render import (
    render_ads_record,
    render_asr_transcript,
    render_notes,
    render_outline,
    render_refined_transcript,
    render_srt,
)
from .utils.text import slugify

if TYPE_CHECKING:
    from .pipeline.context import RunContext

# 变体名前缀（role → 前缀/扩展名）；PDF 按风格单列
_FILE_VARIANTS = {
    "notes": ("笔记", "md"),
    "outline": ("大纲", "md"),
    "raw": ("原始稿", "md"),
    "refined": ("精修稿", "md"),
    "ads": ("省略广告", "txt"),
    "srt": ("字幕", "srt"),
    "meta": ("元数据", "json"),
    "docx": ("Word", "docx"),
    "epub": ("电子书", "epub"),
}


def output_filename(role: str, title: str, *, style: str = "newspaper", mode: str = "full") -> str:
    """按「<变体>版-<视频标题>」规则生成输出文件名。"""
    slug = slugify(title)
    if role == "pdf":
        variant = "论文" if style == "paper" else "报刊"
        if mode == "brief":
            variant += "精简"
        return f"{variant}版-{slug}.pdf"
    prefix, ext = _FILE_VARIANTS.get(role, (role, "bin"))
    return f"{prefix}版-{slug}.{ext}"


def build_export_meta(
    meta: VideoMeta,
    asr: AsrResult,
    refine: RefineResult,
    summary: SummaryResult,
    ads: AdsResult | None = None,
) -> dict:
    payload = {
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
    if ads is not None:
        omitted = sum(item.end_idx - item.start_idx + 1 for item in ads.ranges)
        payload["processing"]["ads"] = {
            "model": ads.model,
            "ranges": len(ads.ranges),
            "segments_omitted": omitted,
            "seconds": round(sum(item.duration_sec for item in ads.ranges), 1),
            "tokens_in": ads.tokens_in,
            "tokens_out": ads.tokens_out,
        }
    return payload


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
    from .translate import load_translation

    ads = load_ads(ctx)
    asr_all, refine_all = asr, refine  # 原始（未过滤）版本：省略记录与元数据用
    asr = apply_ads_to_asr(asr_all, ads)
    refine = apply_ads(refine_all, ads)
    ads_note = ads_note_text(ads)
    translation = load_translation(ctx)
    bilingual = ctx.settings.export.bilingual and translation is not None
    published = meta.pubdate.strftime("%Y-%m-%d") if meta.pubdate else None
    out_dir = ctx.final_output_dir(meta.title, published)
    out_dir.mkdir(parents=True, exist_ok=True)

    notes_path = out_dir / output_filename("notes", meta.title)
    outline_path = out_dir / output_filename("outline", meta.title)
    raw_path = out_dir / output_filename("raw", meta.title)
    refined_path = out_dir / output_filename("refined", meta.title)
    meta_path = out_dir / output_filename("meta", meta.title)

    asr_label = f"{asr.engine} / {asr.model}"
    notes_path.write_text(
        render_notes(meta, summary, asr_model=asr_label, ads_note=ads_note), encoding="utf-8"
    )
    outline_path.write_text(render_outline(meta, summary), encoding="utf-8")
    raw_path.write_text(render_asr_transcript(asr, ads_note=ads_note), encoding="utf-8")
    refined_path.write_text(
        render_refined_transcript(
            refine, translation=translation, bilingual=bilingual, ads_note=ads_note
        ),
        encoding="utf-8",
    )
    meta_path.write_text(
        json.dumps(
            build_export_meta(meta, asr_all, refine_all, summary, ads=ads),
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    artifacts = {
        "notes": str(notes_path.resolve()),
        "outline": str(outline_path.resolve()),
        "output_dir": str(out_dir.resolve()),
    }
    if ads is not None and ads.ranges:
        ads_path = out_dir / output_filename("ads", meta.title)
        ads_path.write_text(
            render_ads_record(meta, ads, refine=refine_all, asr=asr_all), encoding="utf-8"
        )
        artifacts["ads"] = str(ads_path.resolve())
        ctx.log.info("广告省略记录已导出：%s", ads_path.name)

    # ---- 关键帧图片（frames 步骤产物）----
    images_src = ctx.artifact("images")
    images_dst = out_dir / "images"
    figure_count = 0
    point_count = 0
    if images_src.exists():
        images_dst.mkdir(parents=True, exist_ok=True)
        # 清掉旧一轮截图，避免输出目录残留过期图片
        for pattern in ("fig*.jpg", "pt*.jpg"):
            for stale in images_dst.glob(pattern):
                stale.unlink(missing_ok=True)
        shutil.copytree(images_src, images_dst, dirs_exist_ok=True)
        figure_count = sum(1 for _ in images_dst.glob("fig*.jpg"))
        point_count = sum(1 for _ in images_dst.glob("pt*.jpg"))

    # ---- 可选产出：Word / PDF / EPUB ----
    export_cfg = ctx.settings.export
    if export_cfg.docx or export_cfg.pdf or export_cfg.epub:
        from .frames import load_frames
        from .paragraphs import load_paragraphs
        from .pdf import DocData, render_docx, render_pdf
        from .translate import load_translation

        doc_data = DocData(
            meta=meta,
            summary=summary,
            refine=refine,
            asr=asr,
            frames=load_frames(ctx),
            images_dir=images_dst,
            mode=export_cfg.pdf_mode,
            style=export_cfg.pdf_style,
            scale=export_cfg.pdf_scale,
            point_figures=export_cfg.point_figures,
            point_thumb_mm=export_cfg.point_thumb_mm,
            translation=load_translation(ctx),
            bilingual=export_cfg.bilingual,
            paragraphs=load_paragraphs(ctx),
            ads_note=ads_note,
        )
        if export_cfg.docx:
            docx_path = render_docx(doc_data, out_dir / output_filename("docx", meta.title))
            artifacts["docx"] = str(docx_path.resolve())
            ctx.log.info("Word 已导出：%s", docx_path.name)
        if export_cfg.pdf:
            pdf_path = render_pdf(
                doc_data,
                out_dir / output_filename(
                    "pdf", meta.title, style=doc_data.style, mode=doc_data.mode
                ),
            )
            artifacts["pdf"] = str(pdf_path.resolve())
            ctx.log.info(
                "PDF 已导出（%s / %s / 字号 %s）：%s",
                export_cfg.pdf_style,
                export_cfg.pdf_mode,
                export_cfg.pdf_scale,
                pdf_path.name,
            )
        if export_cfg.epub:
            from .epub_export import render_epub

            epub_path = render_epub(doc_data, out_dir / output_filename("epub", meta.title))
            artifacts["epub"] = str(epub_path.resolve())
            ctx.log.info("EPUB 已导出（%s）：%s", export_cfg.pdf_mode, epub_path.name)

    ctx.log.info("配图：章节 %d 张 / 要点 %d 张（images/）", figure_count, point_count)

    if ctx.settings.export.srt:
        srt_path = out_dir / output_filename("srt", meta.title)
        srt_path.write_text(
            render_srt(refine, asr, translation=translation, bilingual=bilingual),
            encoding="utf-8",
        )
        artifacts["srt"] = str(srt_path.resolve())

    removed = cleanup_media(ctx)
    if removed:
        ctx.log.info("已清理媒体文件：%s", "、".join(removed))
    ctx.log.info("导出完成：%s", out_dir)
    return artifacts

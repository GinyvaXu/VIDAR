"""独立重渲染：按现有 work 产物重新导出 PDF / Word，不重跑流水线。

用途：排版快速迭代——改字号档位、风格、篇幅、要点截图密度后立即重出 PDF。
入口：
    CLI: vidar pdf <链接> --style paper --scale compact --point-figures sparse
    GUI: 主窗口「预览排版…」（内嵌 PDF 预览 + 参数面板）
"""

from __future__ import annotations

from pathlib import Path
from typing import cast

from .ads import ads_note_text, apply_ads, load_ads
from .config import Settings
from .errors import VidarError
from .export import output_filename
from .frames import load_frames
from .models import AsrResult, RefineResult, SummaryResult, VideoMeta
from .paragraphs import load_paragraphs
from .pdf import DocData, render_docx, render_pdf
from .pipeline.context import RunContext
from .translate import load_translation

_REQUIRED_MODELS = (
    ("meta.json", VideoMeta),
    ("asr.json", AsrResult),
    ("refine.json", RefineResult),
    ("summary.json", SummaryResult),
)


def load_doc_data(
    settings: Settings,
    source: str,
    *,
    style: str | None = None,
    mode: str | None = None,
    scale: str | float | None = None,
    point_figures: str | None = None,
    point_thumb_mm: float | None = None,
    bilingual: bool | None = None,
) -> tuple[DocData, Path]:
    """从 work 目录组装渲染数据；缺产物时给出可操作的提示。"""
    ctx = RunContext(settings, source)
    missing: list[str] = []
    loaded: dict[str, object] = {}
    for filename, model_type in _REQUIRED_MODELS:
        path = ctx.work_dir / filename
        if not path.exists():
            missing.append(filename)
            continue
        try:
            loaded[filename] = model_type.model_validate_json(path.read_text(encoding="utf-8"))
        except (ValueError, OSError) as exc:
            raise VidarError(
                f"中间产物损坏：{filename}",
                hint=f"可尝试从该步骤重跑：{exc}",
            ) from exc
    if missing:
        raise VidarError(
            f"缺少中间产物：{'、'.join(missing)}",
            hint="这条视频还没跑完整流程；先执行 vidar run <链接>（或 GUI 完整跑一次）",
        )

    meta = cast(VideoMeta, loaded["meta.json"])
    asr = cast(AsrResult, loaded["asr.json"])
    ads = load_ads(ctx)
    refine = apply_ads(cast(RefineResult, loaded["refine.json"]), ads)
    summary = cast(SummaryResult, loaded["summary.json"])

    export_cfg = settings.export
    published = meta.pubdate.strftime("%Y-%m-%d") if meta.pubdate else None
    out_dir = ctx.final_output_dir(meta.title, published)

    images_dir = ctx.work_dir / "images"
    if not images_dir.exists():
        images_dir = out_dir / "images"

    data = DocData(
        meta=meta,
        summary=summary,
        refine=refine,
        asr=asr,
        frames=load_frames(ctx),
        images_dir=images_dir,
        mode=mode or export_cfg.pdf_mode,
        style=style or export_cfg.pdf_style,
        scale=scale if scale is not None else export_cfg.pdf_scale,
        point_figures=point_figures or export_cfg.point_figures,
        point_thumb_mm=(
            point_thumb_mm if point_thumb_mm is not None else export_cfg.point_thumb_mm
        ),
        translation=load_translation(ctx),
        bilingual=bilingual if bilingual is not None else export_cfg.bilingual,
        paragraphs=load_paragraphs(ctx),
        ads_note=ads_note_text(ads),
    )
    return data, out_dir


def rerender_task(
    settings: Settings,
    source: str,
    *,
    style: str | None = None,
    mode: str | None = None,
    scale: str | float | None = None,
    point_figures: str | None = None,
    point_thumb_mm: float | None = None,
    docx: bool = False,
    epub: bool = False,
    bilingual: bool | None = None,
) -> dict[str, Path]:
    """重渲染 PDF（可选 Word / EPUB），返回产物路径字典。"""
    data, out_dir = load_doc_data(
        settings,
        source,
        style=style,
        mode=mode,
        scale=scale,
        point_figures=point_figures,
        point_thumb_mm=point_thumb_mm,
        bilingual=bilingual,
    )
    out_dir.mkdir(parents=True, exist_ok=True)

    artifacts: dict[str, Path] = {}
    pdf_path = out_dir / output_filename(
        "pdf", data.meta.title, style=data.style, mode=data.mode
    )
    artifacts["pdf"] = render_pdf(data, pdf_path)
    if docx:
        docx_path = out_dir / output_filename("docx", data.meta.title)
        artifacts["docx"] = render_docx(data, docx_path)
    if epub:
        from .epub_export import render_epub

        epub_path = out_dir / output_filename("epub", data.meta.title)
        artifacts["epub"] = render_epub(data, epub_path)
    return artifacts


__all__ = ["load_doc_data", "rerender_task"]

"""报刊风 PDF：双栏、报头、要点框、金句、配图。"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    KeepTogether,
    NextPageTemplate,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)
from reportlab.platypus import (
    Image as RLImage,
)

from .. import __version__
from ..utils.text import format_clock
from . import DocData
from .theme import (
    FONT_REGULAR,
    HAIRLINE,
    MUTED,
    RULE,
    draw_fitted_text,
    figure_flowables,
    para,
    points_box,
    quote_flowable,
    register_fonts,
)

PAGE_W, PAGE_H = A4
MARGIN_X = 16 * mm
MARGIN_TOP = 17 * mm
MARGIN_BOTTOM = 16 * mm
GUTTER = 8 * mm
FULL_W = PAGE_W - 2 * MARGIN_X
COL_W = (FULL_W - GUTTER) / 2
TOP_Y = PAGE_H - MARGIN_TOP
COL_GAP = 4 * mm
NUMERALS = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫"


def _build_styles(scale: float = 1.0) -> dict:
    s = scale
    return {
        "info": para("np-info", size=8.5 * s, leading=1.4, align=TA_CENTER, color=MUTED),
        "title": para("np-title", size=23 * s, leading=1.18, align=TA_CENTER, bold=True),
        "lead": para("np-lead", size=12.5 * s, leading=1.5, align=TA_CENTER, color=MUTED),
        "h2": para("np-h2", size=13.5 * s, leading=1.3, bold=True, keep_with_next=True),
        "chapter_lead": para("np-chapter-lead", size=10 * s, leading=1.5, color=MUTED),
        "body": para("np-body", size=10.5 * s, leading=1.65, align=TA_JUSTIFY),
        "quote": para("np-quote", size=10 * s, leading=1.55, color=MUTED),
        "caption": para("np-caption", size=8.5 * s, leading=1.4, align=TA_CENTER, color=MUTED),
        "box_title": para("np-box-title", size=11 * s, leading=1.3, bold=True),
        "box_item": para("np-box-item", size=9.5 * s, leading=1.55),
    }


def _masthead(data: DocData, styles: dict) -> tuple[list, float]:
    """报头 flowables 与精确高度（供首页框架定位）。"""
    meta = data.meta
    info = " · ".join(
        part
        for part in (
            "BILIBILI",
            format_clock(meta.duration_sec or 0),
            meta.up_name,
            meta.pubdate.strftime("%Y-%m-%d") if meta.pubdate else "",
        )
        if part
    )
    parts: list = [Paragraph(escape(info), styles["info"]), Spacer(1, 3)]

    rule = Table([[""]], colWidths=[FULL_W], rowHeights=[1.6])
    rule.setStyle(
        TableStyle(
            [
                ("LINEABOVE", (0, 0), (-1, -1), 1.6, RULE),
                ("LINEBELOW", (0, 0), (-1, -1), 0.4, RULE),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ]
        )
    )
    parts += [rule, Spacer(1, 9), Paragraph(escape(meta.title), styles["title"]), Spacer(1, 7)]
    if data.summary.one_liner:
        parts += [Paragraph(escape(data.summary.one_liner), styles["lead"]), Spacer(1, 9)]

    cover = data.cover_image()
    if cover is not None:
        image = RLImage(str(cover))
        ratio = image.imageHeight / max(image.imageWidth, 1)
        width = FULL_W * 0.72
        height = width * ratio
        if height > 76 * mm:
            height = 76 * mm
            width = height / ratio
        image.drawWidth = width
        image.drawHeight = height
        image.hAlign = "CENTER"
        parts += [image, Paragraph("图 1：视频封面（00:00）", styles["caption"]), Spacer(1, 4)]

    total = 0.0
    for part in parts:
        _, height = part.wrap(FULL_W, 1000 * mm)
        total += height
    return parts, total + 6


def _paint_footer(canvas, doc, data: DocData, *, header: bool) -> None:  # noqa: ANN001
    canvas.saveState()
    canvas.setFillColor(MUTED)
    if header:
        canvas.setStrokeColor(HAIRLINE)
        canvas.setLineWidth(0.4)
        line_y = PAGE_H - MARGIN_TOP + 7
        canvas.line(MARGIN_X, line_y, PAGE_W - MARGIN_X, line_y)
        # 左上角标题：尽量完整显示（自动缩字号，仍放不下才截断）
        title = data.meta.title.replace("\n", " ").strip()
        draw_fitted_text(
            canvas,
            title,
            MARGIN_X,
            line_y + 2.5,
            PAGE_W - 2 * MARGIN_X - 92,
            size=7.5,
        )
        canvas.setFont(FONT_REGULAR, 7.5)
        canvas.drawRightString(PAGE_W - MARGIN_X, line_y + 2.5, "VIDAR 视频文稿")
    footer_y = 10 * mm
    canvas.setFont(FONT_REGULAR, 7.5)
    canvas.drawString(MARGIN_X, footer_y, data.meta.bvid)
    canvas.drawCentredString(PAGE_W / 2, footer_y, f"— {canvas.getPageNumber()} —")
    canvas.drawRightString(PAGE_W - MARGIN_X, footer_y, datetime.now().strftime("%Y-%m-%d"))
    canvas.restoreState()


def build_newspaper(data: DocData) -> Path:
    register_fonts()
    styles = _build_styles(data.scale)
    masthead_parts, masthead_h = _masthead(data, styles)

    col_top = TOP_Y - masthead_h - COL_GAP
    col_height = col_top - MARGIN_BOTTOM
    if col_height < 60 * mm:  # 报头过高时兜底压缩
        col_height = 60 * mm

    def _columns(y_top: float) -> list[Frame]:
        height = y_top - MARGIN_BOTTOM
        return [
            Frame(MARGIN_X, MARGIN_BOTTOM, COL_W, height, id="col1",
                  leftPadding=0, rightPadding=3, topPadding=0, bottomPadding=0),
            Frame(MARGIN_X + COL_W + GUTTER, MARGIN_BOTTOM, COL_W, height, id="col2",
                  leftPadding=3, rightPadding=0, topPadding=0, bottomPadding=0),
        ]

    first_template = PageTemplate(
        id="first",
        frames=[
            Frame(MARGIN_X, col_top + COL_GAP, FULL_W, masthead_h, id="masthead",
                  leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0),
            *_columns(col_top),
        ],
        onPage=lambda canvas, doc: _paint_footer(canvas, doc, data, header=False),
    )
    later_template = PageTemplate(
        id="later",
        frames=_columns(TOP_Y),
        onPage=lambda canvas, doc: _paint_footer(canvas, doc, data, header=True),
    )

    story: list = [NextPageTemplate("later"), *masthead_parts, Spacer(1, 4)]

    if data.summary.key_points:
        thumb_width = min(max(data.point_thumb_mm, 16.0), 40.0) * mm
        items: list = []
        for index, point in enumerate(data.summary.key_points[:12]):
            numeral = NUMERALS[index] if index < len(NUMERALS) else "·"
            stamp = f"　[{format_clock(point.start)}]" if point.start is not None else ""
            items.append((data.point_thumb(index + 1), f"{numeral} {escape(point.text)}{stamp}"))
        story.append(
            points_box(
                "本期看点",
                items,
                title_style=styles["box_title"],
                item_style=styles["box_item"],
                width=COL_W - 6,
                thumb_width=thumb_width,
            )
        )
        story.append(Spacer(1, 9))

    figure_number = 2  # 图 1 是封面
    # 竖版截图高度上限：以首页栏高为基准（换栏/换页前能放下即可）
    max_figure_height = max(150.0, min(col_height, (TOP_Y - MARGIN_BOTTOM)) * 0.85)
    for chapter in data.summary.chapters:
        heading = Paragraph(
            f"{chapter.index + 1} · {escape(chapter.title)}　[{format_clock(chapter.start)}]",
            styles["h2"],
        )
        head_block: list = [heading, Spacer(1, 2)]
        if chapter.summary:
            head_block.append(Paragraph(escape(chapter.summary), styles["chapter_lead"]))
        story.append(KeepTogether(head_block))

        if chapter.quote:
            story += [Spacer(1, 3), quote_flowable(escape(chapter.quote), styles["quote"])]

        for start, caption, path in data.chapter_figures(chapter):
            story += figure_flowables(
                path,
                caption,
                figure_number,
                start,
                COL_W - 8,
                styles["caption"],
                max_height=max_figure_height,
            )
            figure_number += 1

        if data.mode == "full":
            for _start, text in data.chapter_paragraphs(chapter):
                story.append(Paragraph(escape(text), styles["body"]))

        story.append(Spacer(1, 9))

    story += [
        Spacer(1, 4),
        Paragraph(f"原视频：{escape(data.meta.url)}", styles["caption"]),
    ]

    document = BaseDocTemplate(
        str(data.output_path),
        pagesize=A4,
        leftMargin=MARGIN_X,
        rightMargin=MARGIN_X,
        topMargin=MARGIN_TOP,
        bottomMargin=MARGIN_BOTTOM,
        title=data.meta.title,
        author=data.meta.up_name or "VIDAR",
        subject="VIDAR 视频文稿（报刊风）",
        creator=f"VIDAR v{__version__}",
    )
    document.addPageTemplates([first_template, later_template])
    import os as _os

    if _os.environ.get("VIDAR_DEBUG_FRAMES"):
        for template in document.pageTemplates:
            geometry = [
                (
                    frame.id,
                    round(frame._x1),
                    round(frame._y1),
                    round(frame._width),
                    round(frame._height),
                )
                for frame in template.frames
            ]
            print(f"[debug] template {template.id}: {geometry}")
    document.build(story)
    return data.output_path

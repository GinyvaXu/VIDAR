"""论文风 PDF：封面信息、摘要、目录、编号章节、图注、页码。"""

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
    HRFlowable,
    NextPageTemplate,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)
from reportlab.platypus.tableofcontents import TableOfContents

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
    font_safe,
    keyword_line,
    para,
    quote_flowable,
    register_fonts,
)

PAGE_W, PAGE_H = A4
MARGIN_X = 22 * mm
MARGIN_TOP = 22 * mm
MARGIN_BOTTOM = 20 * mm
TEXT_W = PAGE_W - 2 * MARGIN_X
TOP_Y = PAGE_H - MARGIN_TOP


class PaperDoc(BaseDocTemplate):
    """支持目录登记的论文风文档。"""

    def __init__(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
        super().__init__(*args, **kwargs)
        self._chapter_no = 0

    def handle_documentBegin(self) -> None:
        """multiBuild 每遍开始时重置章节计数（否则目录条目跨遍累积、永不收敛）。"""
        self._chapter_no = 0
        super().handle_documentBegin()

    def afterFlowable(self, flowable) -> None:  # noqa: ANN001
        if isinstance(flowable, Paragraph) and flowable.style.name == "pp-h1":
            self._chapter_no += 1
            key = f"chapter-{self._chapter_no}"
            self.canv.bookmarkPage(key)
            self.notify("TOCEntry", (0, flowable.getPlainText(), self.page, key))


def _build_styles(scale: float = 1.0) -> dict:
    s = scale
    return {
        "title": para("pp-title", size=19 * s, leading=1.3, align=TA_CENTER, bold=True),
        "meta": para("pp-meta", size=10 * s, leading=1.5, align=TA_CENTER, color=MUTED),
        "h1": para(
            "pp-h1", size=13.5 * s, leading=1.4, bold=True, space_before=10 * s,
            keep_with_next=True,
        ),
        "sec": para(
            "pp-sec", size=13.5 * s, leading=1.4, bold=True, space_before=10 * s,
            keep_with_next=True,
        ),
        "h2": para(
            "pp-h2", size=11.5 * s, leading=1.4, bold=True, space_before=8 * s,
            keep_with_next=True,
        ),
        "body": para(
            "pp-body",
            size=11 * s,
            leading=1.5,
            align=TA_JUSTIFY,
            space_after=6 * s,
            firstLineIndent=22 * s,
        ),
        "source": para(
            "pp-source",
            size=9 * s,
            leading=1.45,
            color=MUTED,
            space_after=6 * s,
            firstLineIndent=18 * s,
        ),
        "abstract": para("pp-abstract", size=10.5 * s, leading=1.55, align=TA_JUSTIFY),
        "abstract_item": para("pp-abstract-item", size=10.5 * s, leading=1.55),
        "quote": para("pp-quote", size=10.5 * s, leading=1.55, color=MUTED),
        "caption": para("pp-caption", size=9 * s, leading=1.4, align=TA_CENTER, color=MUTED),
        "toc": para("pp-toc", size=10.5 * s, leading=1.9),
        "note": para("pp-note", size=9 * s, leading=1.5, color=MUTED),
    }


def _decorate(canvas, doc, data: DocData) -> None:  # noqa: ANN001
    canvas.saveState()
    canvas.setFillColor(MUTED)
    canvas.setStrokeColor(HAIRLINE)
    canvas.setLineWidth(0.4)
    line_y = PAGE_H - 14 * mm
    canvas.line(MARGIN_X, line_y, PAGE_W - MARGIN_X, line_y)
    # 页眉标题：尽量完整显示（自动缩字号，仍放不下才截断）
    title = data.meta.title.replace("\n", " ").strip()
    draw_fitted_text(
        canvas,
        title,
        MARGIN_X,
        line_y + 2.5,
        PAGE_W - 2 * MARGIN_X - 40,
        size=8,
    )
    canvas.setFont(FONT_REGULAR, 8)
    canvas.drawRightString(PAGE_W - MARGIN_X, line_y + 2.5, "VIDAR")
    canvas.drawCentredString(PAGE_W / 2, 11 * mm, f"— {canvas.getPageNumber()} —")
    canvas.restoreState()


def build_paper(data: DocData) -> Path:
    register_fonts()
    styles = _build_styles(data.scale)
    meta = data.meta

    document = PaperDoc(
        str(data.output_path),
        pagesize=A4,
        leftMargin=MARGIN_X,
        rightMargin=MARGIN_X,
        topMargin=MARGIN_TOP,
        bottomMargin=MARGIN_BOTTOM,
        title=meta.title,
        author=meta.up_name or "VIDAR",
        subject="VIDAR 视频文稿（论文风）",
        creator=f"VIDAR v{__version__}",
    )
    document.addPageTemplates(
        [
            PageTemplate(
                id="body",
                frames=[Frame(MARGIN_X, MARGIN_BOTTOM, TEXT_W, TOP_Y - MARGIN_BOTTOM,
                              leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)],
                onPage=lambda canvas, doc: _decorate(canvas, doc, data),
            )
        ]
    )

    story: list = [NextPageTemplate("body"), Spacer(1, 6)]
    story.append(Paragraph(escape(font_safe(meta.title)), styles["title"]))
    story.append(Spacer(1, 6))
    authors = f"作者：{meta.up_name or '—'}　　整理：VIDAR v{__version__}"
    story.append(Paragraph(escape(authors), styles["meta"]))
    published = meta.pubdate.strftime("%Y-%m-%d") if meta.pubdate else "—"
    story.append(
        Paragraph(
            f"{published} · 时长 {format_clock(meta.duration_sec or 0)} · {meta.bvid}",
            styles["meta"],
        )
    )
    story.append(Spacer(1, 8))
    story.append(HRFlowable(width="100%", thickness=0.8, color=RULE, spaceAfter=10))

    # 摘要
    abstract_rows: list[list] = [[Paragraph("摘　要", styles["sec"])]]
    if data.summary.one_liner:
        abstract_rows.append(
            [Paragraph(escape(font_safe(data.summary.one_liner)), styles["abstract"])]
        )
    if data.summary.key_points:
        abstract_rows.append([Spacer(1, 4)])
        for index, point in enumerate(data.summary.key_points[:5], start=1):
            abstract_rows.append(
                [Paragraph(f"{index}. {escape(point.text)}", styles["abstract_item"])]
            )
    abstract_rows.append([Spacer(1, 4)])
    abstract_rows.append([keyword_line(data.keywords, styles["note"])])
    abstract_box = Table(abstract_rows, colWidths=[TEXT_W])
    abstract_box.setStyle(
        TableStyle(
            [
                ("BOX", (0, 0), (-1, -1), 0.6, HAIRLINE),
                ("LEFTPADDING", (0, 0), (-1, -1), 10),
                ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ]
        )
    )
    story += [abstract_box, Spacer(1, 12)]

    # 目录
    story.append(Paragraph("目　录", styles["sec"]))
    toc = TableOfContents()
    toc.levelStyles = [styles["toc"]]
    story += [toc, Spacer(1, 10)]

    # 正文
    figure_number = 1
    for chapter in data.summary.chapters:
        story.append(Paragraph(f"{chapter.index + 1}　{escape(chapter.title)}", styles["h1"]))
        if chapter.summary:
            story.append(Spacer(1, 2))
            story.append(Paragraph(escape(chapter.summary), styles["abstract"]))

        quote = data.chapter_quote(chapter)
        if quote:
            story += [Spacer(1, 3), quote_flowable(escape(quote), styles["quote"])]

        for start, caption, path in data.chapter_figures(chapter):
            story += figure_flowables(
                path,
                caption,
                figure_number,
                start,
                TEXT_W * 0.82,
                styles["caption"],
                max_height=(TOP_Y - MARGIN_BOTTOM) * 0.8,
            )
            figure_number += 1

        if data.mode == "full":
            for seg_start, text, source in data.chapter_paragraphs(chapter):
                suffix = f' <font size="8" color="#5f5f5f">[{format_clock(seg_start)}]</font>'
                story.append(Paragraph(escape(text) + suffix, styles["body"]))
                if source:
                    story.append(Paragraph(escape(source), styles["source"]))
        story.append(Spacer(1, 8))

    story += [
        HRFlowable(width="100%", thickness=0.5, color=HAIRLINE, spaceBefore=8, spaceAfter=6),
        Paragraph(f"原视频：{escape(meta.url)}", styles["note"]),
        *(
            [Paragraph(escape(data.ads_note), styles["note"])]
            if data.ads_note
            else []
        ),
        Paragraph(
            f"本文档由 VIDAR v{__version__} 生成于 {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            styles["note"],
        ),
    ]

    document.multiBuild(story)
    return data.output_path

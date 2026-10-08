"""PDF 主题：字体注册、段落样式与通用 flowable（两种模板共用）。"""

from __future__ import annotations

from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Image as RLImage
from reportlab.platypus import Paragraph, Spacer, Table, TableStyle

FONT_DIR = Path(__file__).resolve().parent.parent / "assets" / "fonts"
FONT_REGULAR = "NotoSerifSC"
FONT_BOLD = "NotoSerifSC-Bold"

INK = colors.HexColor("#1a1a1a")
MUTED = colors.HexColor("#5f5f5f")
RULE = colors.HexColor("#2b2b2b")
HAIRLINE = colors.HexColor("#9a9a9a")


def register_fonts() -> None:
    """注册内置中文字体（幂等）。"""
    if FONT_BOLD in pdfmetrics.getRegisteredFontNames():
        return
    regular = FONT_DIR / "NotoSerifSC-Regular.ttf"
    bold = FONT_DIR / "NotoSerifSC-Bold.ttf"
    if not regular.exists() or not bold.exists():
        raise FileNotFoundError(
            "缺少内置中文字体（src/vidar/assets/fonts），请先运行 scripts/prepare_fonts.py"
        )
    pdfmetrics.registerFont(TTFont(FONT_REGULAR, str(regular)))
    pdfmetrics.registerFont(TTFont(FONT_BOLD, str(bold)))
    pdfmetrics.registerFontFamily(
        FONT_REGULAR,
        normal=FONT_REGULAR,
        bold=FONT_BOLD,
        italic=FONT_REGULAR,
        boldItalic=FONT_BOLD,
    )


def para(
    name: str,
    *,
    size: float,
    leading: float,
    align: int = TA_LEFT,
    color: colors.Color = INK,
    bold: bool = False,
    space_before: float = 0,
    space_after: float = 0,
    keep_with_next: bool = False,
    **extra: object,
) -> ParagraphStyle:
    return ParagraphStyle(
        name,
        fontName=FONT_BOLD if bold else FONT_REGULAR,
        fontSize=size,
        leading=size * leading,
        alignment=align,
        textColor=color,
        spaceBefore=space_before,
        spaceAfter=space_after,
        keepWithNext=1 if keep_with_next else 0,
        allowWidows=0,
        allowOrphans=0,
        **extra,  # type: ignore[arg-type]
    )


def figure_flowables(
    path: Path,
    caption: str,
    number: int,
    start: float,
    width: float,
    caption_style: ParagraphStyle,
    max_height: float | None = None,
) -> list:
    """配图 + 图注（含时间戳）；max_height 用于限制竖版截图高度（等比缩放）。"""
    from ..utils.text import format_clock

    image = RLImage(str(path))
    ratio = image.imageHeight / max(image.imageWidth, 1)
    image.drawWidth = width
    image.drawHeight = width * ratio
    if max_height is not None and image.drawHeight > max_height:
        image.drawHeight = max_height
        image.drawWidth = max_height / ratio
    image.hAlign = "CENTER"
    label = (
        f"图 {number}：{caption}（{format_clock(start)}）"
        if caption
        else f"图 {number}（{format_clock(start)}）"
    )
    return [Spacer(1, 5), image, Paragraph(label, caption_style), Spacer(1, 7)]


def quote_flowable(text: str, style: ParagraphStyle) -> Table:
    """金句引用块：左侧粗线 + 灰字。"""
    table = Table([[Paragraph(text, style)]], colWidths=["100%"])
    table.setStyle(
        TableStyle(
            [
                ("LINEBEFORE", (0, 0), (0, -1), 2.5, RULE),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 2),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]
        )
    )
    return table


def points_box(
    title: str,
    points: list[str],
    *,
    title_style: ParagraphStyle,
    item_style: ParagraphStyle,
    width: float,
) -> Table:
    """「本期看点」要点框：细边框、无底色（打印友好）。"""
    rows = [[Paragraph(title, title_style)]] + [
        [Paragraph(point, item_style)] for point in points
    ]
    table = Table(rows, colWidths=[width])
    table.setStyle(
        TableStyle(
            [
                ("BOX", (0, 0), (-1, -1), 0.7, RULE),
                ("LINEBELOW", (0, 0), (0, 0), 0.4, HAIRLINE),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    return table


def keyword_line(keywords: list[str], style: ParagraphStyle) -> Paragraph:
    joined = " / ".join(keywords) if keywords else "—"
    return Paragraph(f"关键词：{joined}", style)


__all__ = [
    "FONT_DIR",
    "FONT_REGULAR",
    "FONT_BOLD",
    "INK",
    "MUTED",
    "RULE",
    "HAIRLINE",
    "register_fonts",
    "para",
    "figure_flowables",
    "quote_flowable",
    "points_box",
    "keyword_line",
]

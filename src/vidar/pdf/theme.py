"""PDF 主题：字体注册、段落样式与通用 flowable（两种模板共用）。"""

from __future__ import annotations

from pathlib import Path
from xml.sax.saxutils import escape

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


def font_safe(text: str) -> str:
    """剔除内置字体（GB2312 子集）不覆盖的码点（emoji/生僻字等），避免豆腐块。

    注意：可安全作用于「已 XML 转义」的文本（ASCII 标记字符会原样保留）。
    """
    if not text:
        return text
    out: list[str] = []
    for char in text:
        if char in "\n\t":
            out.append(char)
            continue
        try:
            char.encode("gb2312")
        except UnicodeEncodeError:
            continue
        out.append(char)
    return "".join(out)

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


def draw_fitted_text(
    canvas,  # noqa: ANN001 - reportlab canvas
    text: str,
    x: float,
    y: float,
    max_width: float,
    *,
    font: str = FONT_REGULAR,
    size: float = 7.5,
    min_size: float = 5.5,
) -> None:
    """在 max_width 内尽量完整地绘制文本：先自动缩小字号，仍放不下才截断。"""
    text = font_safe(text)
    current = size
    while current > min_size + 1e-9 and pdfmetrics.stringWidth(text, font, current) > max_width:
        current = max(min_size, current - 0.25)
    if pdfmetrics.stringWidth(text, font, current) > max_width:
        while text and pdfmetrics.stringWidth(text + "…", font, current) > max_width:
            text = text[:-1]
        text += "…"
    canvas.setFont(font, current)
    canvas.drawString(x, y, text)


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
        f"图 {number}：{font_safe(caption)}（{format_clock(start)}）"
        if caption
        else f"图 {number}（{format_clock(start)}）"
    )
    return [Spacer(1, 5), image, Paragraph(escape(label), caption_style), Spacer(1, 7)]


def quote_flowable(text: str, style: ParagraphStyle) -> Table:
    """金句引用块：左侧粗线 + 灰字（text 需已 XML 转义）。"""
    table = Table([[Paragraph(font_safe(text), style)]], colWidths=["100%"])
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
    points: list,
    *,
    title_style: ParagraphStyle,
    item_style: ParagraphStyle,
    width: float,
    thumb_width: float = 0.0,
) -> Table:
    """「本期看点」要点框：细边框、无底色（打印友好）。

    points 元素为 str，或 (缩略图 Path | None, str) 二元组（带要点截图）。
    thumb_width > 0 且存在截图时，自动切换为「左图右文」两列布局。
    """
    has_thumb = thumb_width > 0 and any(
        isinstance(item, tuple) and item[0] is not None for item in points
    )
    rows: list[list] = [[Paragraph(font_safe(title), title_style)]]
    for item in points:
        thumb, text = item if isinstance(item, tuple) else (None, item)
        if has_thumb:
            rows.append(
                [_thumb_flowable(thumb, thumb_width), Paragraph(font_safe(text), item_style)]
            )
        else:
            rows.append([Paragraph(font_safe(text), item_style)])

    col_widths = [thumb_width, width - thumb_width] if has_thumb else [width]
    table = Table(rows, colWidths=col_widths, splitByRow=1)
    commands = [
        ("BOX", (0, 0), (-1, -1), 0.7, RULE),
        ("LINEBELOW", (0, 0), (-1, 0), 0.4, HAIRLINE),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]
    if has_thumb:
        commands.append(("SPAN", (0, 0), (1, 0)))
        commands.append(("VALIGN", (0, 0), (-1, -1), "TOP"))
    table.setStyle(TableStyle(commands))
    return table


def _thumb_flowable(path, width: float):  # noqa: ANN001 - Path | None（避免顶层 import Path 仅为此）
    """要点缩略图：宽度固定，高度上限为正方形（竖版截图不外扩）。"""
    if path is None:
        return Spacer(1, 1)
    image = RLImage(str(path))
    ratio = image.imageHeight / max(image.imageWidth, 1)
    image.drawWidth = width
    image.drawHeight = width * ratio
    if image.drawHeight > width:
        image.drawHeight = width
        image.drawWidth = width / ratio
    image.hAlign = "CENTER"
    return image


def keyword_line(keywords: list[str], style: ParagraphStyle) -> Paragraph:
    joined = " / ".join(keywords) if keywords else "—"
    return Paragraph(f"关键词：{escape(font_safe(joined))}", style)


__all__ = [
    "FONT_DIR",
    "FONT_REGULAR",
    "FONT_BOLD",
    "INK",
    "MUTED",
    "RULE",
    "HAIRLINE",
    "register_fonts",
    "font_safe",
    "para",
    "draw_fitted_text",
    "figure_flowables",
    "quote_flowable",
    "points_box",
    "keyword_line",
]

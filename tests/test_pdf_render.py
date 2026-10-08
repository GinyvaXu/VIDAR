"""PDF / Word 渲染测试：合成数据 + 真实字体，不需要网络与视频。"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest
from PIL import Image

from vidar.frames import ExtractedFrame
from vidar.models import (
    AsrResult,
    Chapter,
    FigureHint,
    KeyPoint,
    RefinedSegment,
    RefineResult,
    SummaryResult,
    VideoMeta,
)
from vidar.pdf import DocData, render_docx, render_pdf


def _doc_data(tmp_path: Path) -> DocData:
    images = tmp_path / "images"
    images.mkdir(exist_ok=True)
    Image.new("RGB", (640, 360), (120, 140, 160)).save(images / "cover.jpg")
    # 竖版截图（手机录屏场景），验证高度封顶逻辑
    Image.new("RGB", (360, 640), (80, 90, 100)).save(images / "fig01.jpg")

    meta = VideoMeta(
        bvid="BV1xx411c7mD",
        title="测试视频：排版渲染",
        up_name="测试UP",
        url="https://www.bilibili.com/video/BV1xx411c7mD",
        pubdate=datetime(2026, 5, 1),
        duration_sec=754.0,
        tags=["测试", "排版"],
    )
    segments = [
        RefinedSegment(
            idx=index,
            start=index * 10.0,
            end=index * 10.0 + 9.0,
            raw_text=f"原始第{index}段",
            refined_text=f"精修后的第{index}段文字，用于排版测试。",
        )
        for index in range(4)
    ]
    chapters = [
        Chapter(
            index=0,
            title="开场与背景",
            start=0.0,
            end=20.0,
            summary="介绍主题与背景。",
            quote="这是一句金句。",
            figures=[FigureHint(start=10.0, caption="示意图", anchor=1)],
        ),
        Chapter(index=1, title="核心内容", start=20.0, end=40.0, summary="展开核心内容。"),
    ]
    summary = SummaryResult(
        model="fake",
        one_liner="一句话结论。",
        key_points=[KeyPoint(text="要点一", start=10.0), KeyPoint(text="要点二", start=30.0)],
        chapters=chapters,
    )
    asr = AsrResult(engine="fake", model="fake", segments=[])
    frames = [ExtractedFrame(index=1, start=10.0, caption="示意图", filename="fig01.jpg")]
    return DocData(
        meta=meta,
        summary=summary,
        refine=RefineResult(model="fake", segments=segments),
        asr=asr,
        frames=frames,
        images_dir=images,
    )


@pytest.mark.parametrize("style", ["newspaper", "paper"])
def test_render_pdf(tmp_path: Path, style: str) -> None:
    data = _doc_data(tmp_path)
    data.style = style
    output = tmp_path / f"{style}.pdf"
    render_pdf(data, output)

    assert output.exists()
    assert output.stat().st_size > 2000

    import pymupdf  # type: ignore[import-not-found]

    with pymupdf.open(str(output)) as document:
        assert document.page_count >= 1
        text = "".join(
            document[index].get_text() for index in range(min(3, document.page_count))
        )
    assert "测试视频" in text
    if style == "newspaper":
        assert "本期看点" in text
    else:
        assert "摘" in text and "目" in text


def test_chapter_quote_dedupe(tmp_path: Path) -> None:
    data = _doc_data(tmp_path)
    chapter = data.summary.chapters[0]
    chapter.summary = "这是一句金句。补充说明。"
    assert data.chapter_quote(chapter) == ""


def test_render_docx(tmp_path: Path) -> None:
    data = _doc_data(tmp_path)
    output = tmp_path / "notes.docx"
    render_docx(data, output)

    assert output.exists()
    assert output.stat().st_size > 2000

    from docx import Document

    document = Document(str(output))
    text = "\n".join(paragraph.text for paragraph in document.paragraphs)
    assert "测试视频" in text
    assert "一句话结论" in text


# --------------------------------------------------------------------------- #
# 0.5.0：字号档位 / 文件名规则 / 要点截图 / 长标题页眉
# --------------------------------------------------------------------------- #
def test_resolve_scale() -> None:
    from vidar.pdf import resolve_scale

    assert resolve_scale("compact") == 0.85
    assert resolve_scale("standard") == 1.0
    assert resolve_scale("large") == 1.18
    assert resolve_scale(0.9) == 0.9
    assert resolve_scale("0.75") == 0.75
    assert resolve_scale("bogus") == 1.0
    assert resolve_scale(9.9) == 1.5  # 夹取上限


def test_output_filename_rules() -> None:
    from vidar.export import output_filename

    # Windows 非法字符（ASCII 冒号）被剔除；全角「：」是合法字符，保留
    assert output_filename("pdf", "测试:标题", style="newspaper", mode="full") == (
        "报刊版-测试标题.pdf"
    )
    assert output_filename("pdf", "测试：标题", style="paper", mode="brief") == (
        "论文精简版-测试：标题.pdf"
    )
    assert output_filename("notes", "测试") == "笔记版-测试.md"
    assert output_filename("outline", "测试") == "大纲版-测试.md"
    assert output_filename("raw", "测试") == "原始稿版-测试.md"
    assert output_filename("refined", "测试") == "精修稿版-测试.md"
    assert output_filename("docx", "测试") == "Word版-测试.docx"
    assert output_filename("meta", "测试") == "元数据版-测试.json"


def test_point_thumb_density_filter(tmp_path: Path) -> None:
    data = _doc_data(tmp_path)
    Image.new("RGB", (640, 360), (200, 120, 90)).save(data.images_dir / "pt01.jpg")
    Image.new("RGB", (640, 360), (90, 160, 120)).save(data.images_dir / "pt02.jpg")
    data.frames.extend(
        [
            ExtractedFrame(
                index=2, start=10.0, caption="要点一", filename="pt01.jpg",
                kind="point", point_index=1,
            ),
            ExtractedFrame(
                index=3, start=30.0, caption="要点二", filename="pt02.jpg",
                kind="point", point_index=2,
            ),
        ]
    )
    assert data.point_figures == "all"
    assert data.point_thumb(1) is not None and data.point_thumb(2) is not None

    data.point_figures = "sparse"
    assert data.point_thumb(1) is not None
    assert data.point_thumb(2) is None

    data.point_figures = "off"
    assert data.point_thumb(1) is None


def test_newspaper_render_with_point_thumbs(tmp_path: Path) -> None:
    data = _doc_data(tmp_path)
    Image.new("RGB", (640, 360), (200, 120, 90)).save(data.images_dir / "pt01.jpg")
    data.frames.append(
        ExtractedFrame(
            index=2, start=10.0, caption="要点一", filename="pt01.jpg",
            kind="point", point_index=1,
        )
    )
    data.scale = 0.85  # 紧凑档位（小字，一页更多内容）

    output = tmp_path / "np-thumbs.pdf"
    render_pdf(data, output)
    assert output.exists()
    assert output.stat().st_size > 2000

    import pymupdf  # type: ignore[import-not-found]

    with pymupdf.open(str(output)) as document:
        assert document.page_count >= 1
        text = "".join(document[index].get_text() for index in range(document.page_count))
    assert "本期看点" in text


def test_long_title_header_not_truncated(tmp_path: Path) -> None:
    """用户反馈：页眉标题显示不全 —— 长标题应完整出现在第二页页眉。"""
    data = _doc_data(tmp_path)
    long_title = "给孩子的信：不要追求“快乐人生” 悲与喜的哲学（完整标题不截断测试）"
    data.meta.title = long_title
    data.style = "paper"
    # 加长内容以产生第二页（页眉只在第 2 页起出现）
    data.refine.segments = [
        RefinedSegment(
            idx=index,
            start=index * 10.0,
            end=index * 10.0 + 9.0,
            raw_text=f"原始第{index}段",
            refined_text=f"精修后的第{index}段文字，用于制造足够长的正文以分页。",
        )
        for index in range(60)
    ]

    output = tmp_path / "paper-long-title.pdf"
    render_pdf(data, output)

    import pymupdf  # type: ignore[import-not-found]

    with pymupdf.open(str(output)) as document:
        assert document.page_count >= 2
        header_text = document[1].get_text()
    assert long_title in header_text.replace("\u00a0", " "), "第二页页眉应完整显示标题"

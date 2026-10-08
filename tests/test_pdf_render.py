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

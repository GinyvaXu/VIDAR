"""渲染层测试：notes / outline / 逐字稿 / SRT / 跳转链接。"""

from __future__ import annotations

from datetime import datetime

from vidar.models import (
    AsrResult,
    AsrSegment,
    Chapter,
    KeyPoint,
    RefinedSegment,
    RefineResult,
    SummaryResult,
    VideoMeta,
)
from vidar.render import (
    note_link,
    render_asr_transcript,
    render_notes,
    render_outline,
    render_refined_transcript,
    render_srt,
)


def _meta() -> VideoMeta:
    return VideoMeta(
        bvid="BV1xx411c7mD",
        title="测试视频 | 带竖线",
        up_name="测试UP",
        url="https://www.bilibili.com/video/BV1xx411c7mD",
        pubdate=datetime(2024, 5, 1),
        duration_sec=3661.0,
    )


def _chapters() -> list[Chapter]:
    return [
        Chapter(index=0, title="开场", start=0.0, end=100.0, summary="介绍主题"),
        Chapter(index=1, title="核心内容", start=100.0, end=200.0, summary="展开讲解"),
    ]


def _summary() -> SummaryResult:
    return SummaryResult(
        model="deepseek-chat",
        one_liner="一句话结论。",
        key_points=[
            KeyPoint(text="要点一", start=10.0),
            KeyPoint(text="要点二", start=150.0),
            KeyPoint(text="无锚点要点", start=None),
        ],
        chapters=_chapters(),
    )


def test_note_link() -> None:
    assert note_link("https://b23.tv/x", 61.7) == "https://b23.tv/x?t=61"
    assert note_link("https://b23.tv/x?p=2", 61.7) == "https://b23.tv/x?p=2&t=61"
    assert note_link("https://b23.tv/x", None) == ""


def test_render_notes() -> None:
    text = render_notes(_meta(), _summary(), asr_model="faster-whisper / large-v3")
    assert 'title: "测试视频 | 带竖线"' in text
    assert "bvid: BV1xx411c7mD" in text
    assert "> **一句话结论：** 一句话结论。" in text
    assert "（[00:10](https://www.bilibili.com/video/BV1xx411c7mD?t=10)）" in text
    assert (
        "| [01:40](https://www.bilibili.com/video/BV1xx411c7mD?t=100) | 核心内容 | 展开讲解 |"
        in text
    )
    assert "### 1. 开场" in text


def test_render_outline() -> None:
    text = render_outline(_meta(), _summary())
    assert "- 开场（00:00）" in text
    assert "  - 要点一" in text
    assert "  - 要点二" in text
    assert "## 补充要点" in text
    assert "- 无锚点要点" in text


def test_render_transcripts_and_srt() -> None:
    asr = AsrResult(
        engine="faster-whisper",
        model="large-v3",
        duration_sec=12.0,
        segments=[AsrSegment(idx=0, start=1.0, end=3.5, text="原始文本。")],
    )
    refine = RefineResult(
        model="deepseek-chat",
        tokens_in=10,
        tokens_out=5,
        segments=[
            RefinedSegment(
                idx=0, start=1.0, end=3.5, raw_text="原始文本。", refined_text="精修文本。"
            )
        ],
    )
    assert "[00:01]" in render_asr_transcript(asr)
    assert "精修模型：deepseek-chat" in render_refined_transcript(refine)

    srt = render_srt(refine, asr)
    assert "00:00:01,000 --> 00:00:03,500" in srt
    assert "精修文本。" in srt

    srt_raw = render_srt(None, asr)
    assert "原始文本。" in srt_raw

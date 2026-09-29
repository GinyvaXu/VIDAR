"""文本工具测试。"""

from __future__ import annotations

from vidar.models import AsrSegment
from vidar.utils.text import (
    chunk_by_chars,
    estimate_tokens,
    format_clock,
    format_srt_time,
    parse_bvid,
    slugify,
    split_sentences,
)


def test_format_clock() -> None:
    assert format_clock(0) == "00:00"
    assert format_clock(65) == "01:05"
    assert format_clock(3661) == "1:01:01"
    assert format_clock(3661, pad=True) == "01:01:01"
    assert format_clock(-5) == "00:00"


def test_format_srt_time() -> None:
    assert format_srt_time(0) == "00:00:00,000"
    assert format_srt_time(3661.5) == "01:01:01,500"
    assert format_srt_time(-1) == "00:00:00,000"


def test_split_sentences() -> None:
    text = "大家好。今天我们聊一聊AI！你觉得呢？"
    assert split_sentences(text) == ["大家好。", "今天我们聊一聊AI！", "你觉得呢？"]
    assert split_sentences("") == []
    assert split_sentences("没有句号") == ["没有句号"]


def test_slugify() -> None:
    assert slugify('a/b:c*d?e"f<g>h|i') == "abcdefghi"
    assert slugify("   ") == "untitled"
    assert len(slugify("字" * 60)) == 40
    assert slugify("CON") == "_CON"
    assert slugify("标题 带 空格").count(" ") == 2


def test_parse_bvid() -> None:
    assert parse_bvid("https://www.bilibili.com/video/BV1xx411c7mD?p=2") == "BV1xx411c7mD"
    assert parse_bvid("BV1xx411c7mD") == "BV1xx411c7mD"
    assert parse_bvid("没有 BV 号") is None


def test_estimate_tokens() -> None:
    assert estimate_tokens("你好世界") == 4
    assert estimate_tokens("hello world") >= 1
    assert estimate_tokens("") == 0


def test_chunk_by_chars() -> None:
    segments = [
        AsrSegment(idx=i, start=float(i), end=float(i + 1), text="x" * 100) for i in range(5)
    ]
    chunks = chunk_by_chars(segments, max_chars=250)
    assert [len(chunk) for chunk in chunks] == [2, 2, 1]

    single = [AsrSegment(idx=0, start=0.0, end=1.0, text="x" * 500)]
    assert len(chunk_by_chars(single, max_chars=250)) == 1  # 单条超预算不切分

"""广告识别：解析/合并/过滤重编号/记录渲染（无网络）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from vidar.config import Settings
from vidar.models import (
    AdRange,
    AdsResult,
    AsrResult,
    AsrSegment,
    RefinedSegment,
    RefineResult,
    VideoMeta,
)


def _refine() -> RefineResult:
    return RefineResult(
        model="fake",
        segments=[
            RefinedSegment(idx=0, start=0.0, end=5.0, raw_text="大家好", refined_text="大家好。"),
            RefinedSegment(
                idx=1, start=5.0, end=12.0, raw_text="广告口播", refined_text="本视频由XX赞助。"
            ),
            RefinedSegment(
                idx=2, start=12.0, end=20.0, raw_text="正片", refined_text="我们继续讲内容。"
            ),
        ],
    )


def _ads() -> AdsResult:
    return AdsResult(
        model="fake",
        ranges=[
            AdRange(start_idx=1, end_idx=1, start=5.0, end=12.0, reason="赞助口播"),
        ],
    )


def test_parse_ranges_variants() -> None:
    from vidar.ads import _parse_ranges

    payload = {
        "ads": [
            {"start": 1, "end": 2, "reason": "r"},
            {"start_index": "3", "end_index": 4},
            [5, "6"],
            {"start": 9, "end": 8},  # 逆序自动纠正
            {"start": None, "end": 3},
            "垃圾",
        ]
    }
    assert _parse_ranges(payload) == [(1, 2, "r"), (3, 4, ""), (5, 6, ""), (8, 9, "")]


def test_parse_ranges_missing_key_raises() -> None:
    from vidar.ads import _parse_ranges
    from vidar.errors import LlmError

    with pytest.raises(LlmError):
        _parse_ranges({"points": []})


def test_merge_ranges_overlap() -> None:
    from vidar.ads import merge_ranges

    assert merge_ranges([(1, 2, "a"), (2, 5, "b"), (7, 8, "c")]) == [(1, 5, "a"), (7, 8, "c")]


def test_apply_ads_renumbers() -> None:
    from vidar.ads import apply_ads

    filtered = apply_ads(_refine(), _ads())
    assert [seg.idx for seg in filtered.segments] == [0, 1]
    assert filtered.segments[1].refined_text == "我们继续讲内容。"
    assert filtered.segments[1].start == 12.0  # 时间戳保持原值
    # 无广告（None / 空区间）：原样返回
    assert apply_ads(_refine(), None).segments[1].idx == 1
    assert apply_ads(_refine(), AdsResult(model="m")).segments[1].idx == 1


def test_apply_ads_to_asr() -> None:
    from vidar.ads import apply_ads_to_asr

    asr = AsrResult(
        engine="e",
        model="m",
        segments=[
            AsrSegment(idx=0, start=0.0, end=1.0, text="a"),
            AsrSegment(idx=1, start=1.0, end=2.0, text="b"),
        ],
    )
    filtered = apply_ads_to_asr(asr, _ads())
    assert [seg.idx for seg in filtered.segments] == [0]


def test_ads_note_text() -> None:
    from vidar.ads import ads_note_text

    assert ads_note_text(None) == ""
    assert ads_note_text(AdsResult(model="m")) == ""
    note = ads_note_text(_ads())
    assert "1 段广告" in note and "00:07" in note  # 5→12 秒，合计 7 秒


def test_load_refine_effective(monkeypatch, tmp_path: Path) -> None:  # noqa: ANN001
    monkeypatch.chdir(tmp_path)
    from vidar.ads import load_refine_effective
    from vidar.pipeline.context import RunContext

    ctx = RunContext(Settings(), "BV1aa411c7mD")
    ctx.artifact("refine.json").write_text(_refine().model_dump_json(), encoding="utf-8")
    assert [s.idx for s in load_refine_effective(ctx).segments] == [0, 1, 2]

    ctx.artifact("ads.json").write_text(_ads().model_dump_json(), encoding="utf-8")
    filtered = load_refine_effective(ctx)
    assert [s.idx for s in filtered.segments] == [0, 1]
    assert filtered.segments[1].refined_text == "我们继续讲内容。"


def test_detect_ads_with_fake_client(monkeypatch, tmp_path: Path) -> None:  # noqa: ANN001
    monkeypatch.chdir(tmp_path)
    import vidar.ads as ads_module
    from vidar.pipeline.context import RunContext

    class _FakeUsage:
        prompt_tokens = 10
        completion_tokens = 5

    class _FakeClient:
        def __init__(self, settings) -> None:  # noqa: ANN001
            self.usage = _FakeUsage()

        def complete_json(self, prompt: str, *, system: str = "", model: str | None = None) -> dict:
            assert "赞助" in prompt or "广告" in prompt
            return {"ads": [{"start": 1, "end": 1, "reason": "赞助"}]}

    monkeypatch.setattr(ads_module, "LlmClient", _FakeClient)
    ctx = RunContext(Settings(), "BV1aa411c7mD")
    result = ads_module.detect_ads(ctx, _refine())
    assert len(result.ranges) == 1
    item = result.ranges[0]
    assert (item.start_idx, item.end_idx) == (1, 1)
    assert item.start == 5.0 and item.end == 12.0  # 本地映射回时间戳
    assert not ctx.artifact("ads.partial.json").exists()


def test_render_ads_record_and_notes() -> None:
    from vidar.render import render_ads_record, render_asr_transcript, render_refined_transcript

    meta = VideoMeta(
        bvid="BV1aa411c7mD",
        title="测试视频",
        url="https://www.bilibili.com/video/BV1aa411c7mD",
    )
    record = render_ads_record(meta, _ads(), refine=_refine())
    assert "本视频由XX赞助。" in record
    assert "00:05" in record and "赞助口播" in record
    assert "测试视频" in record

    note = "本稿已自动省略 1 段广告（合计 00:07）。"
    asr = AsrResult(
        engine="e",
        model="m",
        segments=[AsrSegment(idx=1, start=5.0, end=12.0, text="本视频由XX赞助。")],
    )
    raw = render_asr_transcript(asr, ads_note=note)
    assert raw.strip().endswith(note)

    refined = render_refined_transcript(_refine(), ads_note=note)
    assert refined.strip().endswith(note)

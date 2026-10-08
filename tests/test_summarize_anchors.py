"""要点锚点恢复测试：模糊匹配 + 相邻插值（不调用 LLM）。"""

from __future__ import annotations

import logging
from types import SimpleNamespace

from vidar.models import KeyPoint
from vidar.summarize import _fill_missing_starts, _recover_anchor


def _ctx() -> SimpleNamespace:
    return SimpleNamespace(log=logging.getLogger("test"))


def test_normalize_anchor_formats() -> None:
    from vidar.summarize import _normalize_anchor

    assert _normalize_anchor(12) == 12
    assert _normalize_anchor("71-85") == 71  # 区间取起始段号
    assert _normalize_anchor("26") == 26
    assert _normalize_anchor(12.0) == 12
    assert _normalize_anchor("段[90]") == 90
    assert _normalize_anchor(None) is None
    assert _normalize_anchor(True) is None
    assert _normalize_anchor("无") is None


def test_recover_anchor_by_bigram_overlap() -> None:
    points = [
        {"text": "生命以 conatus 努力保存自身，并把环境组织成周围世界。", "anchor": 7},
        {"text": "成长是可供性不断收缩。", "anchor": 12},
    ]
    # reduce 改写后仍能通过二元组重叠匹配到段号
    assert _recover_anchor("生命以 conatus 维持自身，把环境组织成关切场。", points) == 7
    assert _recover_anchor("完全无关的另一段内容。", points) is None


def test_fill_missing_starts_interpolates_and_extrapolates() -> None:
    key_points = [
        KeyPoint(text="a", start=100.0),
        KeyPoint(text="b", start=None),
        KeyPoint(text="c", start=None),
        KeyPoint(text="d", start=400.0),
        KeyPoint(text="e", start=None),
    ]
    _fill_missing_starts(key_points, _ctx())  # type: ignore[arg-type]

    starts = [point.start for point in key_points]
    assert starts[1] == 200.0 and starts[2] == 300.0  # 已知点之间：等距插值
    assert starts[4] == 460.0  # 尾部：最近已知点 +60s
    assert all(value is not None for value in starts)


def test_fill_missing_starts_noop_when_all_known() -> None:
    key_points = [KeyPoint(text="a", start=1.0), KeyPoint(text="b", start=2.0)]
    _fill_missing_starts(key_points, _ctx())  # type: ignore[arg-type]
    assert [point.start for point in key_points] == [1.0, 2.0]

"""语段编辑测试：LLM 编组校验、兜底、边界修正、渲染回退。"""

from __future__ import annotations

import re
from pathlib import Path

from vidar.config import Settings
from vidar.models import (
    Chapter,
    ParagraphGroup,
    ParagraphResult,
    RefinedSegment,
    RefineResult,
    SummaryResult,
    TranslatedSegment,
    TranslateResult,
    VideoMeta,
)
from vidar.paragraphs import _enforce_bounds, _parse_groups, build_paragraphs
from vidar.pdf import DocData
from vidar.pipeline import RunContext


class _FakeUsage:
    prompt_tokens = 10
    completion_tokens = 5


class _FakeGroupClient:
    """每 2 句合成一段（按提示词里的段号）。"""

    def __init__(self, settings) -> None:  # noqa: ANN001
        self.usage = _FakeUsage()

    def complete_json(self, prompt: str, *, system: str = "", model: str | None = None) -> dict:
        indices = [int(m.group(1)) for m in re.finditer(r"^\[(\d+)\]", prompt, re.MULTILINE)]
        groups = []
        for i in range(0, len(indices), 2):
            chunk = indices[i : i + 2]
            groups.append({"start_index": chunk[0], "end_index": chunk[-1]})
        return {"paragraphs": groups}


class _BadGroupClient(_FakeGroupClient):
    def complete_json(self, prompt: str, *, system: str = "", model: str | None = None) -> dict:
        return {"paragraphs": [{"start_index": 0, "end_index": 999}]}


def _make_ctx(monkeypatch, tmp_path: Path) -> RunContext:  # noqa: ANN001
    monkeypatch.chdir(tmp_path)
    settings = Settings()
    settings.paragraph.min_chars = 5
    settings.paragraph.max_chars = 40
    return RunContext(settings, "BV1xx411c7mD")


def _refine(count: int = 4) -> RefineResult:
    return RefineResult(
        model="fake",
        segments=[
            RefinedSegment(
                idx=i,
                start=i * 5.0,
                end=i * 5.0 + 4.0,
                raw_text=f"raw{i}",
                refined_text=f"原句{i}。",
            )
            for i in range(count)
        ],
    )


# --------------------------------------------------------------------------- #
# 解析与校验
# --------------------------------------------------------------------------- #
def test_parse_groups_validation() -> None:
    valid = [0, 1, 2, 3]
    assert _parse_groups(
        {"paragraphs": [{"start_index": 0, "end_index": 1}, {"start_index": 2, "end_index": 3}]},
        valid,
    ) == [[0, 1], [2, 3]]

    # 漏句 / 重叠 / 越界 / 乱序 全部判为非法
    assert _parse_groups({"paragraphs": [{"start_index": 0, "end_index": 0}]}, valid) is None
    overlap = {
        "paragraphs": [
            {"start_index": 0, "end_index": 1},
            {"start_index": 1, "end_index": 3},
        ]
    }
    assert _parse_groups(overlap, valid) is None
    assert _parse_groups({"paragraphs": [{"start_index": 0, "end_index": 9}]}, valid) is None
    assert _parse_groups({"paragraphs": [{"start_index": 3, "end_index": 1}]}, valid) is None
    assert _parse_groups({"paragraphs": []}, valid) is None


def test_enforce_bounds_merges_and_splits() -> None:
    rows = {
        i: (i, float(i), float(i + 1), "字" * length, "")
        for i, length in enumerate([10, 10, 10, 60, 60, 10])
    }
    groups = _enforce_bounds(
        [[0], [1], [2], [3, 4], [5]], rows, min_chars=25, max_chars=100
    )
    lengths = [sum(len(rows[i][3]) for i in g) for g in groups]
    assert all(length <= 100 for length in lengths)
    assert sum(lengths) == 160  # 不丢内容
    assert [idx for g in groups for idx in g] == [0, 1, 2, 3, 4, 5]  # 顺序保持


# --------------------------------------------------------------------------- #
# LLM 编组 + 兜底
# --------------------------------------------------------------------------- #
def test_build_paragraphs_with_llm(monkeypatch, tmp_path: Path) -> None:  # noqa: ANN001
    ctx = _make_ctx(monkeypatch, tmp_path)
    monkeypatch.setattr("vidar.paragraphs.LlmClient", _FakeGroupClient)

    result = build_paragraphs(ctx, _refine(4), None)
    assert len(result.paragraphs) == 2
    first = result.paragraphs[0]
    assert first.segments == [0, 1]
    assert first.text == "原句0。原句1。"
    assert first.source_text == "原句0。原句1。"  # 无译文时原文=正文
    assert first.start == 0.0


def test_build_paragraphs_fallback(monkeypatch, tmp_path: Path) -> None:  # noqa: ANN001
    ctx = _make_ctx(monkeypatch, tmp_path)
    monkeypatch.setattr("vidar.paragraphs.LlmClient", _BadGroupClient)

    result = build_paragraphs(ctx, _refine(4), None)
    # 非法结果 → 机械兜底；仍覆盖全部句号
    covered = [idx for para in result.paragraphs for idx in para.segments]
    assert covered == [0, 1, 2, 3]


def test_build_paragraphs_prefers_translation(monkeypatch, tmp_path: Path) -> None:  # noqa: ANN001
    ctx = _make_ctx(monkeypatch, tmp_path)
    monkeypatch.setattr("vidar.paragraphs.LlmClient", _FakeGroupClient)

    translation = TranslateResult(
        model="m",
        source_language="en",
        target_language="zh",
        segments=[
            TranslatedSegment(
                idx=i, start=i * 5.0, end=i * 5.0 + 4.0, source_text=f"原句{i}。",
                translated_text=f"译{i}。",
            )
            for i in range(4)
        ],
    )
    result = build_paragraphs(ctx, _refine(4), translation)
    assert result.paragraphs[0].text == "译0。译1。"  # 正文用译文
    assert result.paragraphs[0].source_text == "原句0。原句1。"  # 原文对照保留


# --------------------------------------------------------------------------- #
# 渲染接入
# --------------------------------------------------------------------------- #
def _doc_data(tmp_path: Path, *, with_paragraphs: bool, bilingual: bool = False) -> DocData:
    refine = _refine(4)
    chapters = [Chapter(index=0, title="开篇", start=0.0, end=30.0, summary="小结。")]
    paragraphs = None
    if with_paragraphs:
        paragraphs = ParagraphResult(
            model="m",
            paragraphs=[
                ParagraphGroup(
                    index=0, start=0.0, end=9.0,
                    text="第0句内容。第1句内容。", source_text="原文第0句。原文第1句。",
                    segments=[0, 1],
                ),
                ParagraphGroup(
                    index=1, start=10.0, end=19.0,
                    text="第2句内容。第3句内容。", source_text="原文第2句。原文第3句。",
                    segments=[2, 3],
                ),
            ],
        )
    return DocData(
        meta=VideoMeta(
            bvid="BV1xx411c7mD", title="语段测试", url="https://example.com/v",
            duration_sec=20.0,
        ),
        summary=SummaryResult(model="m", chapters=chapters),
        refine=refine,
        asr=None,  # type: ignore[arg-type]
        frames=[],
        images_dir=tmp_path,
        bilingual=bilingual,
        paragraphs=paragraphs,
    )


def test_docdata_grouped_paragraphs(tmp_path: Path) -> None:
    data = _doc_data(tmp_path, with_paragraphs=True)
    rows = data.chapter_paragraphs(data.summary.chapters[0])
    assert rows == [
        (0.0, "第0句内容。第1句内容。", ""),
        (10.0, "第2句内容。第3句内容。", ""),
    ]

    # 旧任务（无语段产物）回退逐句
    legacy = _doc_data(tmp_path, with_paragraphs=False)
    legacy_rows = legacy.chapter_paragraphs(legacy.summary.chapters[0])
    assert len(legacy_rows) == 4

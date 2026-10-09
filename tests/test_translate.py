"""翻译（双语对照）测试：语言判定、翻译步骤、双语渲染——假 LLM，无需网络。"""

from __future__ import annotations

import re
from pathlib import Path

from vidar.config import Settings
from vidar.models import (
    AsrResult,
    AsrSegment,
    Chapter,
    RefinedSegment,
    RefineResult,
    SummaryResult,
    TranslatedSegment,
    TranslateResult,
    VideoMeta,
)
from vidar.pdf import DocData, render_pdf
from vidar.pipeline import PipelineRunner, RunContext
from vidar.pipeline.steps import TranslateStep
from vidar.render import render_refined_transcript, render_srt
from vidar.translate import (
    apply_translation,
    language_name,
    load_translation,
    normalize_language,
    should_translate,
)


class _FakeUsage:
    prompt_tokens = 88
    completion_tokens = 44


class _FakeTranslateClient:
    """按提示词契约返回：译文 = EN<原文>。"""

    def __init__(self, settings) -> None:  # noqa: ANN001 - 与真实客户端签名兼容
        self.usage = _FakeUsage()

    def complete_json(self, prompt: str, *, system: str = "", model: str | None = None) -> dict:
        if '"segments"' not in prompt:
            raise AssertionError(f"未预期的提示词：{prompt[:120]}")
        items = []
        for line in prompt.splitlines():
            match = re.match(r"^\[(\d+)\]\s*(.*)$", line)
            if match:
                items.append({"idx": int(match.group(1)), "text": f"EN<{match.group(2)}>"})
        return {"segments": items}


def _make_ctx(monkeypatch, tmp_path: Path, *, language: str = "en") -> RunContext:  # noqa: ANN001
    monkeypatch.chdir(tmp_path)
    settings = Settings()
    ctx = RunContext(settings, "BV1xx411c7mD")
    asr = AsrResult(
        engine="fake",
        model="fake",
        language=language,
        duration_sec=30.0,
        segments=[
            AsrSegment(idx=0, start=0.0, end=8.0, text="Hello everyone."),
            AsrSegment(idx=1, start=8.0, end=18.0, text="Today we talk about AI."),
            AsrSegment(idx=2, start=18.0, end=30.0, text="Let us begin."),
        ],
    )
    ctx.artifact("asr.json").write_text(asr.model_dump_json(), encoding="utf-8")
    refine = RefineResult(
        model="fake",
        segments=[
            RefinedSegment(
                idx=seg.idx,
                start=seg.start,
                end=seg.end,
                raw_text=seg.text,
                refined_text=seg.text,
            )
            for seg in asr.segments
        ],
    )
    ctx.artifact("refine.json").write_text(refine.model_dump_json(), encoding="utf-8")
    return ctx


# --------------------------------------------------------------------------- #
# 语言判定
# --------------------------------------------------------------------------- #
def test_language_helpers() -> None:
    assert normalize_language("zh-CN") == "zh"
    assert normalize_language("zh_Hans") == "zh"
    assert normalize_language("EN") == "en"
    assert normalize_language("auto") == ""
    assert language_name("zh") == "简体中文"
    assert language_name("ja") == "日文"

    assert should_translate("en", "zh") is True
    assert should_translate("zh", "zh") is False
    assert should_translate("EN", "zh") is True
    assert should_translate("", "zh") is False  # 未知源语言保守跳过
    assert should_translate("zh", "en") is True


# --------------------------------------------------------------------------- #
# 翻译步骤（假 LLM）
# --------------------------------------------------------------------------- #
def test_translate_step_writes_mapping(monkeypatch, tmp_path: Path) -> None:  # noqa: ANN001
    ctx = _make_ctx(monkeypatch, tmp_path, language="en")
    monkeypatch.setattr("vidar.translate.LlmClient", _FakeTranslateClient)

    PipelineRunner(ctx, [TranslateStep()]).run()  # type: ignore[arg-type]

    stored = load_translation(ctx)
    assert stored is not None
    assert stored.source_language == "en" and stored.target_language == "zh"
    assert [seg.idx for seg in stored.segments] == [0, 1, 2]
    assert stored.segments[0].translated_text == "EN<Hello everyone.>"
    assert stored.segments[1].source_text == "Today we talk about AI."


def test_translate_step_skips_same_language(monkeypatch, tmp_path: Path) -> None:  # noqa: ANN001
    ctx = _make_ctx(monkeypatch, tmp_path, language="zh")
    monkeypatch.setattr("vidar.translate.LlmClient", _FakeTranslateClient)

    PipelineRunner(ctx, [TranslateStep()]).run()  # type: ignore[arg-type]

    assert not ctx.artifact("translate.json").exists()


def test_translate_step_removes_stale_output(monkeypatch, tmp_path: Path) -> None:  # noqa: ANN001
    ctx = _make_ctx(monkeypatch, tmp_path, language="zh")
    ctx.artifact("translate.json").write_text("{}", encoding="utf-8")

    PipelineRunner(ctx, [TranslateStep()]).run()  # type: ignore[arg-type]

    assert not ctx.artifact("translate.json").exists()


def test_translate_chunking(monkeypatch, tmp_path: Path) -> None:  # noqa: ANN001
    ctx = _make_ctx(monkeypatch, tmp_path, language="en")
    ctx.settings.translate.max_chars = 20  # 强制多块
    monkeypatch.setattr("vidar.translate.LlmClient", _FakeTranslateClient)

    PipelineRunner(ctx, [TranslateStep()]).run()  # type: ignore[arg-type]

    stored = load_translation(ctx)
    assert stored is not None
    assert len(stored.segments) == 3
    assert all(seg.translated_text.startswith("EN<") for seg in stored.segments)


def test_apply_translation_replaces_text() -> None:
    refine = RefineResult(
        model="m",
        segments=[
            RefinedSegment(idx=0, start=0.0, end=1.0, raw_text="a", refined_text="hello"),
            RefinedSegment(idx=1, start=1.0, end=2.0, raw_text="b", refined_text="world"),
        ],
    )
    translation = TranslateResult(
        model="m",
        source_language="en",
        target_language="zh",
        segments=[
            TranslatedSegment(
                idx=0,
                start=0.0,
                end=1.0,
                source_text="hello",
                translated_text="你好",
            ),
        ],
    )
    applied = apply_translation(refine, translation)
    assert [seg.refined_text for seg in applied.segments] == ["你好", "world"]  # 缺失段回退原文


# --------------------------------------------------------------------------- #
# 双语渲染
# --------------------------------------------------------------------------- #
def _translation() -> TranslateResult:
    return TranslateResult(
        model="m",
        source_language="en",
        target_language="zh",
        segments=[
            TranslatedSegment(
                idx=0, start=0.0, end=8.0, source_text="Hello everyone.", translated_text="大家好。"
            ),
            TranslatedSegment(
                idx=1,
                start=8.0,
                end=18.0,
                source_text="Today AI.",
                translated_text="今天我们聊 AI。",
            ),
        ],
    )


def test_bilingual_srt_and_transcript() -> None:
    refine = RefineResult(
        model="m",
        segments=[
            RefinedSegment(idx=0, start=0.0, end=8.0, raw_text="r", refined_text="Hello everyone."),
            RefinedSegment(idx=1, start=8.0, end=18.0, raw_text="r", refined_text="Today AI."),
        ],
    )
    asr = AsrResult(engine="fake", model="fake", language="en", segments=[])
    srt = render_srt(refine, asr, translation=_translation(), bilingual=True)
    assert "大家好。" in srt and "Hello everyone." in srt
    # 双语行（译文在上、原文在下）
    assert "大家好。\nHello everyone." in srt

    # 关闭双语：只出译文（文档语言 = 目标语言）
    srt_plain = render_srt(refine, asr, translation=_translation(), bilingual=False)
    assert "大家好。" in srt_plain
    assert "Hello everyone." not in srt_plain

    transcript = render_refined_transcript(
        refine, translation=_translation(), bilingual=True
    )
    assert "大家好。" in transcript and "*Hello everyone.*" in transcript

    # 精修稿（逐字稿性质）：关闭双语时保留原文
    plain = render_refined_transcript(refine, translation=_translation(), bilingual=False)
    assert "大家好。" not in plain and "Hello everyone." in plain


def _doc_data(tmp_path: Path, *, bilingual: bool = True) -> DocData:
    images = tmp_path / "images"
    images.mkdir(parents=True, exist_ok=True)
    meta = VideoMeta(
        bvid="BV1xx411c7mD",
        title="双语排版测试",
        up_name="TestUP",
        url="https://www.bilibili.com/video/BV1xx411c7mD",
        duration_sec=20.0,
    )
    chapters = [Chapter(index=0, title="开篇", start=0.0, end=30.0, summary="小结。")]
    summary = SummaryResult(model="m", one_liner="结论。", key_points=[], chapters=chapters)
    refine = RefineResult(
        model="m",
        segments=[
            RefinedSegment(idx=0, start=0.0, end=8.0, raw_text="r", refined_text="Hello everyone."),
            RefinedSegment(idx=1, start=8.0, end=18.0, raw_text="r", refined_text="Today AI."),
        ],
    )
    return DocData(
        meta=meta,
        summary=summary,
        refine=refine,
        asr=AsrResult(engine="fake", model="fake", language="en", segments=[]),
        frames=[],
        images_dir=images,
        translation=_translation(),
        bilingual=bilingual,
    )


def test_chapter_paragraphs_bilingual(tmp_path: Path) -> None:
    data = _doc_data(tmp_path)
    rows = data.chapter_paragraphs(data.summary.chapters[0])
    assert rows == [
        (0.0, "大家好。", "Hello everyone."),
        (8.0, "今天我们聊 AI。", "Today AI."),
    ]

    # 关闭双语：正文仍是译文，只是不再附原文
    plain = _doc_data(tmp_path / "plain", bilingual=False)
    rows_plain = plain.chapter_paragraphs(plain.summary.chapters[0])
    assert rows_plain[0] == (0.0, "大家好。", "")


def test_bilingual_pdf_renders_both_languages(tmp_path: Path) -> None:
    output = tmp_path / "bilingual.pdf"
    render_pdf(_doc_data(tmp_path), output)

    import pymupdf  # type: ignore[import-not-found]

    with pymupdf.open(str(output)) as document:
        text = "".join(document[index].get_text() for index in range(document.page_count))
    assert "大家好。" in text
    assert "Hello everyone." in text

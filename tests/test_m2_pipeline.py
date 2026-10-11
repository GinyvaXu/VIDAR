"""M2 集成测试：用假 LLM 跑通 refine → chapters → summarize → export。

假客户端严格按提示词契约返回 JSON（精修回显、章节单章、要点单条），
可以验证整条链路与产物渲染，不需要网络与 API Key。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from vidar.config import Settings
from vidar.models import AsrResult, AsrSegment, VideoMeta
from vidar.pipeline import PipelineRunner, RunContext, default_steps


class _FakeUsage:
    prompt_tokens = 120
    completion_tokens = 60


class _FakeLlmClient:
    """行为契约与 LlmClient 一致：complete_json(prompt, system=..., model=...)。"""

    def __init__(self, settings) -> None:  # noqa: ANN001 - 与真实客户端签名兼容
        self.usage = _FakeUsage()

    def complete_json(self, prompt: str, *, system: str = "", model: str | None = None) -> dict:
        if '"ads"' in prompt:  # ads：把第 1 段标记为广告
            return {"ads": [{"start": 1, "end": 1, "reason": "赞助口播"}]}
        if '"one_liner"' in prompt:  # reduce
            return {
                "one_liner": "一句话结论",
                "key_points": [{"text": "核心要点", "anchor": 0}],
                "chapter_summaries": [{"chapter_index": 0, "summary": "章节小结"}],
            }
        if '"refined"' in prompt:  # refine：逐段回显
            items = []
            for line in prompt.splitlines():
                match = re.match(r"^\[(\d+)\]\s*(.*)$", line)
                if match:
                    items.append({"index": int(match.group(1)), "text": match.group(2)})
            return {"refined": items}
        if '"chapters"' in prompt:  # chapters
            match = re.search(r"^\[(\d+)\]", prompt, re.MULTILINE)
            start = int(match.group(1)) if match else 0
            return {"chapters": [{"title": "开场", "start_index": start}]}
        if '"paragraphs"' in prompt:  # paragraphs：每 2 句一段
            indices = [int(m.group(1)) for m in re.finditer(r"^\[(\d+)\]", prompt, re.MULTILINE)]
            groups = []
            for i in range(0, len(indices), 2):
                chunk = indices[i : i + 2]
                groups.append({"start_index": chunk[0], "end_index": chunk[-1]})
            return {"paragraphs": groups}
        if '"points"' in prompt:  # map
            match = re.search(r"^\[(\d+)\]", prompt, re.MULTILINE)
            return {"points": [{"text": "片段要点", "anchor": int(match.group(1)) if match else 0}]}
        raise AssertionError(f"未预期的提示词：{prompt[:120]}")


def _make_context(monkeypatch, tmp_path: Path) -> RunContext:  # noqa: ANN001
    monkeypatch.chdir(tmp_path)
    settings = Settings()
    # 本测试只验证 M2 链路（refine→…→export）：关闭截图，避免依赖真实网络下载视频
    settings.export.screenshots = False
    ctx = RunContext(settings, "BV1xx411c7mD")
    meta = VideoMeta(
        bvid="BV1xx411c7mD",
        title="集成测试视频",
        up_name="测试UP",
        url="https://www.bilibili.com/video/BV1xx411c7mD",
        duration_sec=30.0,
    )
    asr = AsrResult(
        engine="fake",
        model="fake",
        duration_sec=30.0,
        segments=[
            AsrSegment(idx=0, start=0.0, end=8.0, text="大家好，今天我们聊人工智能。"),
            AsrSegment(idx=1, start=8.0, end=18.0, text="第一个话题是语音识别。"),
            AsrSegment(idx=2, start=18.0, end=30.0, text="第二个话题是自然语言处理。"),
        ],
    )
    ctx.save_meta(meta)
    ctx.artifact("asr.json").write_text(asr.model_dump_json(), encoding="utf-8")
    for module in (
        "vidar.refine",
        "vidar.ads",
        "vidar.chapters",
        "vidar.summarize",
        "vidar.paragraphs",
    ):
        monkeypatch.setattr(f"{module}.LlmClient", _FakeLlmClient)
    return ctx


def test_m2_end_to_end_with_fake_llm(monkeypatch, tmp_path: Path) -> None:
    ctx = _make_context(monkeypatch, tmp_path)
    runner = PipelineRunner(ctx, default_steps())  # type: ignore[arg-type]
    runner.run(from_step="refine")

    assert (ctx.work_dir / "refine.json").exists()
    assert (ctx.work_dir / "ads.json").exists()
    assert (ctx.work_dir / "chapters.json").exists()
    assert (ctx.work_dir / "summary.json").exists()
    assert (ctx.work_dir / "paragraphs.json").exists()

    notes_files = list((tmp_path / "output").glob("*/笔记版-*.md"))
    assert notes_files, "笔记版 Markdown 未生成"
    notes = notes_files[0].read_text(encoding="utf-8")
    assert "一句话结论" in notes
    assert "核心要点" in notes
    assert "## 章节导航" in notes
    assert "?t=0" in notes  # 跳转链接
    assert "本稿已自动省略 1 段广告" in notes  # 文末一句

    outline = next((notes_files[0].parent).glob("大纲版-*.md")).read_text(encoding="utf-8")
    assert "- 开场（00:00）" in outline

    # 广告段（第 1 号段）已从原始稿 / 精修稿省略，但进入「省略广告」记录
    raw = next((notes_files[0].parent).glob("原始稿版-*.md")).read_text(encoding="utf-8")
    refined = next((notes_files[0].parent).glob("精修稿版-*.md")).read_text(encoding="utf-8")
    assert "第一个话题" not in raw and "第一个话题" not in refined
    assert "第二个话题" in refined
    record = next((notes_files[0].parent).glob("省略广告版-*.txt")).read_text(encoding="utf-8")
    assert "第一个话题是语音识别。" in record and "赞助口播" in record
    meta_file = next((notes_files[0].parent).glob("元数据版-*.json"))
    meta_payload = json.loads(meta_file.read_text(encoding="utf-8"))
    assert meta_payload["processing"]["ads"]["ranges"] == 1


def test_refine_resilient_to_big_chunk_failure(monkeypatch, tmp_path: Path) -> None:  # noqa: ANN001
    """主块失败不炸整步：小批补修成功，仍失败的部分回退原文。"""
    import vidar.refine as refine_module
    from vidar.errors import LlmError
    from vidar.refine import refine_asr_result

    class _FakeUsage:
        prompt_tokens = 1
        completion_tokens = 1

    class _FakeClient:
        def __init__(self, settings) -> None:  # noqa: ANN001
            self.usage = _FakeUsage()

        def complete_json(self, prompt: str, *, system: str = "", model: str | None = None) -> dict:
            lines = [line for line in prompt.splitlines() if re.match(r"^\[\d+\]", line)]
            if len(lines) > 3:  # 大块：模拟网关失败（超时/空返回）
                raise LlmError("模拟大块失败")
            items = []
            for line in lines:
                match = re.match(r"^\[(\d+)\]\s*(.*)$", line)
                if match:
                    items.append(
                        {"index": int(match.group(1)), "text": match.group(2) + "（精修）"}
                    )
            return {"refined": items}

    monkeypatch.setattr(refine_module, "LlmClient", _FakeClient)
    monkeypatch.chdir(tmp_path)
    ctx = RunContext(Settings(), "BV1cc411c7mD")
    asr = AsrResult(
        engine="fake",
        model="fake",
        segments=[
            AsrSegment(idx=i, start=float(i * 10), end=float(i * 10 + 9), text="字" * 150)
            for i in range(8)
        ],
    )
    result = refine_asr_result(ctx, asr)
    assert len(result.segments) == 8
    refined_count = sum(1 for seg in result.segments if seg.refined_text.endswith("（精修）"))
    assert refined_count == 3  # 小批补修成功的 3 段
    assert all(seg.raw_text == "字" * 150 for seg in result.segments)  # 原文始终保留


def test_resume_skips_done_steps(monkeypatch, tmp_path: Path) -> None:
    from vidar.pipeline.steps import ChaptersStep, ExportStep, RefineStep, SummarizeStep

    ctx = _make_context(monkeypatch, tmp_path)
    m2_steps = [RefineStep(), ChaptersStep(), SummarizeStep(), ExportStep()]
    PipelineRunner(ctx, m2_steps).run()  # type: ignore[arg-type]
    assert ctx.state.is_done("export")

    # 二次运行：全部已完成，不允许再调用 LLM（假客户端会直接抛错）
    class _ExplodingClient:
        def __init__(self, settings) -> None:  # noqa: ANN001
            raise AssertionError("已完成步骤不应再次调用 LLM")

    for module in ("vidar.refine", "vidar.chapters", "vidar.summarize"):
        monkeypatch.setattr(f"{module}.LlmClient", _ExplodingClient)

    ctx2 = RunContext(ctx.settings, "BV1xx411c7mD")
    PipelineRunner(ctx2, m2_steps).run()  # type: ignore[arg-type]
    assert ctx2.state.is_done("export")

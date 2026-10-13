"""回归测试：代码审查（2026-10-13）后的修复防护。

覆盖：ASR 续跑时间戳偏移、检查点幻听过滤、分 P 任务键、dry-run 不污染状态、
配置指纹自动重跑、环境变量 Key 不落盘、BOM 配置、模型必需文件动态化、广告变化重置下游。
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from vidar.config import Settings


def _ctx(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, source: str = "BV1cc411c7mD"):  # noqa: ANN202
    monkeypatch.chdir(tmp_path)
    from vidar.pipeline.context import RunContext

    return RunContext(Settings(), source)


# --------------------------------------------------------------------------- #
# ASR 续跑（切片音频时间戳平移 / 检查点幻听过滤）
# --------------------------------------------------------------------------- #
def test_asr_resume_offset_applied(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from vidar.asr import _build_result

    ctx = _ctx(tmp_path, monkeypatch)
    saved = [{"start": 0.0, "end": 10.0, "text": "前半段内容。", "words": []}]
    raw = [
        SimpleNamespace(
            start=2.0,
            end=4.0,
            text="后半段内容。",
            words=[SimpleNamespace(start=2.0, end=3.0, word="后", probability=0.9)],
        )
    ]
    result = _build_result(ctx, ctx.settings.asr, saved, raw, "zh", offset=10.0)
    assert len(result.segments) == 2  # 间隔 ≥1.2s：不合并
    assert result.segments[1].start == 12.0  # 切片相对时间平移到整段音频
    assert result.segments[1].end == 14.0
    assert result.segments[1].words[0].start == 12.0


def test_asr_checkpoint_hallucinations_filtered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from vidar.asr import _build_result

    ctx = _ctx(tmp_path, monkeypatch)
    saved = [
        {"start": 0.0, "end": 1.0, "text": "请点赞投币", "words": []},
        {"start": 1.0, "end": 5.0, "text": "正常的内容。", "words": []},
    ]
    result = _build_result(ctx, ctx.settings.asr, saved, [], "zh")
    assert len(result.segments) == 1
    assert "正常的内容" in result.segments[0].text


# --------------------------------------------------------------------------- #
# 分 P 任务键 / 无 scheme 地址保留 ?p=
# --------------------------------------------------------------------------- #
def test_make_key_includes_page_number() -> None:
    from vidar.pipeline.context import make_key

    assert make_key("BV1xx411c7mD") == "BV1xx411c7mD"
    assert make_key("https://www.bilibili.com/video/BV1xx411c7mD") == "BV1xx411c7mD"
    assert make_key("https://www.bilibili.com/video/BV1xx411c7mD?p=2") == "BV1xx411c7mD-p2"


def test_normalize_source_keeps_page() -> None:
    from vidar.errors import VidarError
    from vidar.source import normalize_source

    assert normalize_source("BV1xx411c7mD") == "https://www.bilibili.com/video/BV1xx411c7mD"
    assert (
        normalize_source("www.bilibili.com/video/BV1xx411c7mD?p=2")
        == "https://www.bilibili.com/video/BV1xx411c7mD?p=2"
    )
    with pytest.raises(VidarError):
        normalize_source("不是链接")


# --------------------------------------------------------------------------- #
# 流水线：dry-run 不污染状态 / 配置指纹变化自动重跑
# --------------------------------------------------------------------------- #
class _PlainStep:
    name = "a"
    title = "A"
    milestone = None

    def run(self, ctx) -> dict[str, str]:  # noqa: ANN001
        return {}


def test_dry_run_does_not_reset_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from vidar.pipeline.base import PipelineRunner
    from vidar.pipeline.state import StepStatus

    ctx = _ctx(tmp_path, monkeypatch)
    PipelineRunner(ctx, [_PlainStep()]).run()  # type: ignore[arg-type]
    assert ctx.state.status("a") is StepStatus.DONE

    PipelineRunner(ctx, [_PlainStep()]).run(from_step="a", dry_run=True)  # type: ignore[arg-type]
    assert ctx.state.status("a") is StepStatus.DONE  # dry-run 不应重置


def test_config_fingerprint_triggers_rerun(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from vidar.pipeline.base import PipelineRunner

    ctx = _ctx(tmp_path, monkeypatch)
    calls: list[int] = []

    class _FpStep:
        name = "a"
        title = "A"
        milestone = None

        def config_fingerprint(self, ctx) -> str:  # noqa: ANN001
            return str(ctx.settings.ui.theme)

        def run(self, ctx) -> dict[str, str]:  # noqa: ANN001
            calls.append(1)
            return {}

    PipelineRunner(ctx, [_FpStep()]).run()  # type: ignore[arg-type]
    assert len(calls) == 1

    PipelineRunner(ctx, [_FpStep()]).run()  # type: ignore[arg-type]
    assert len(calls) == 1  # 指纹未变 → 跳过

    ctx.settings.ui.theme = "dark"
    PipelineRunner(ctx, [_FpStep()]).run()  # type: ignore[arg-type]
    assert len(calls) == 2  # 指纹变化 → 自动重跑


# --------------------------------------------------------------------------- #
# 环境变量 Key 不落盘 / 配置 BOM 防御
# --------------------------------------------------------------------------- #
def test_env_key_not_persisted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("VIDAR_LLM_API_KEY", "sk-env-test-123")
    from vidar.config import export_settings_dict, load_settings

    settings = load_settings(None)
    assert settings.llm.api_key == "sk-env-test-123"
    assert export_settings_dict(settings)["llm"]["api_key"] == ""  # 环境变量来源不落盘

    custom = settings.model_copy(deep=True)
    custom.llm.api_key = "sk-user-input"
    assert export_settings_dict(custom)["llm"]["api_key"] == "sk-user-input"


def test_config_with_bom_loads(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.toml").write_bytes(
        b'\xef\xbb\xbf[paths]\noutput_dir = "out-from-bom"\n'
    )
    from vidar.config import load_settings

    settings = load_settings(None)
    assert str(settings.paths.output_dir) == "out-from-bom"


# --------------------------------------------------------------------------- #
# 模型必需文件动态化（vocabulary.txt/json 二选一）
# --------------------------------------------------------------------------- #
def test_required_files_vocabulary_variants() -> None:
    from vidar.model_store import required_files

    base = {"model.bin": 1, "config.json": 1, "tokenizer.json": 1}
    assert required_files({**base, "vocabulary.txt": 1})[-1] == "vocabulary.txt"
    assert required_files({**base, "vocabulary.json": 1})[-1] == "vocabulary.json"
    assert required_files(None)[-1] == "vocabulary.json"  # 未知仓库按惯例


# --------------------------------------------------------------------------- #
# 广告区间出现/变化：下游自动重置（含老任务首次补跑 ads）
# --------------------------------------------------------------------------- #
def test_reset_from_clears_partial_checkpoints(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = _ctx(tmp_path, monkeypatch)
    partial = ctx.artifact("translate.partial.json")
    partial.write_text("{}", encoding="utf-8")
    ctx.state.reset_from("translate", ["refine", "translate", "export"])
    assert not partial.exists()


def test_ads_step_resets_downstream_on_range_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import vidar.ads as ads_module
    from vidar.models import RefinedSegment, RefineResult, StepStatus
    from vidar.pipeline.steps import AdsStep

    ctx = _ctx(tmp_path, monkeypatch, "BV1aa411c7mD")
    refine = RefineResult(
        model="fake",
        segments=[
            RefinedSegment(idx=0, start=0.0, end=5.0, raw_text="a", refined_text="a"),
            RefinedSegment(idx=1, start=5.0, end=9.0, raw_text="b", refined_text="b"),
        ],
    )
    ctx.artifact("refine.json").write_text(refine.model_dump_json(), encoding="utf-8")

    # 模拟老任务：下游已完成（旧编号）
    for name in ("translate", "chapters", "summarize", "paragraphs", "frames", "export"):
        ctx.state.mark_done(name, {})

    class _FakeUsage:
        prompt_tokens = 1
        completion_tokens = 1

    class _FakeClient:
        def __init__(self, settings) -> None:  # noqa: ANN001
            self.usage = _FakeUsage()

        def complete_json(self, prompt: str, *, system: str = "", model: str | None = None) -> dict:
            return {"ads": [{"start": 1, "end": 1, "reason": "赞助"}]}

    monkeypatch.setattr(ads_module, "LlmClient", _FakeClient)
    AdsStep().run(ctx)

    assert (ctx.work_dir / "ads.json").exists()
    assert ctx.state.status("translate") is StepStatus.PENDING  # 下游被重置重算
    assert ctx.state.status("export") is StepStatus.PENDING
    assert ctx.state.status("ads") is StepStatus.PENDING  # 本步也重置，交给 runner 重新标记

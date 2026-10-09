"""A2/A3/A4 服务层测试：模型推荐、LLM 引导、批量队列（无网络）。"""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

from vidar.config import LlmSettings, Settings
from vidar.errors import VidarError


# --------------------------------------------------------------------------- #
# A2：模型目录与推荐
# --------------------------------------------------------------------------- #
def test_model_catalog_and_find() -> None:
    from vidar.model_store import catalog, find_spec

    specs = catalog()
    assert [spec.alias for spec in specs][:3] == ["large-v3", "large-v3-turbo", "medium"]
    assert all(spec.approx_mb > 0 and spec.vram_gb > 0 for spec in specs)
    assert find_spec("small") is not None
    assert find_spec("不存在") is None


def test_recommend_model_by_vram() -> None:
    from vidar.model_store import recommend_model

    spec, reason = recommend_model(12.0, has_cuda=True)
    assert spec.alias == "large-v3" and "12.0" in reason

    spec, _ = recommend_model(4.5, has_cuda=True)
    assert spec.alias == "large-v3-turbo"

    spec, _ = recommend_model(2.2, has_cuda=True)
    assert spec.alias == "small"

    spec, reason = recommend_model(None, has_cuda=True)
    assert spec.alias == "large-v3-turbo" and "保守" in reason

    spec, reason = recommend_model(None, has_cuda=False)
    assert spec.alias == "small" and "CPU" in reason


def test_detect_vram(monkeypatch) -> None:  # noqa: ANN001
    from vidar.model_store import detect_vram_gb

    fake = SimpleNamespace(returncode=0, stdout="12288\n", stderr="")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: fake)
    assert detect_vram_gb() == 12.0

    fake_bad = SimpleNamespace(returncode=1, stdout="", stderr="boom")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: fake_bad)
    assert detect_vram_gb() is None

    def _raise(*args, **kwargs):  # noqa: ANN002, ANN003
        raise FileNotFoundError("nvidia-smi")

    monkeypatch.setattr(subprocess, "run", _raise)
    assert detect_vram_gb() is None


# --------------------------------------------------------------------------- #
# A3：LLM 引导（预设 / 错误建议 / 前置校验）
# --------------------------------------------------------------------------- #
def test_provider_presets() -> None:
    from vidar.llm.providers import PRESETS, detect_preset, find_preset

    assert len(PRESETS) >= 5
    assert find_preset("deepseek") is not None
    assert find_preset("OPencode-go".lower()) is not None
    assert find_preset("不存在") is None

    preset = detect_preset("https://api.deepseek.com/v1/")
    assert preset is not None and preset.key == "deepseek"
    assert detect_preset("https://custom.example.com/v1") is None


def test_connection_hints() -> None:
    from vidar.llm.providers import _hint_for_error

    assert "API Key" in _hint_for_error("Error code: 401 - invalid api key")
    assert "额度" in _hint_for_error("429 Too Many Requests")
    assert "模型名" in _hint_for_error("404 model not found")
    assert "超时" in _hint_for_error("Request timed out")
    assert "Ollama" in _hint_for_error("Connection refused")
    assert _hint_for_error("某种未知错误") == ""


def test_connection_precheck() -> None:
    from vidar.llm.providers import test_connection

    report = test_connection(LlmSettings(api_base="", api_key="sk-x"))
    assert report.ok is False and "api_base" in report.message

    report = test_connection(LlmSettings(api_base="https://api.example.com/v1", api_key=""))
    assert report.ok is False and "api_key" in report.message

    # 本地服务允许空 Key：能走到网络层（无网络时也应返回失败报告而不是抛异常）
    report = test_connection(
        LlmSettings(api_base="http://127.0.0.1:9/v1", api_key="", timeout_sec=1)
    )
    assert report.ok is False and report.message


# --------------------------------------------------------------------------- #
# A4：批量队列
# --------------------------------------------------------------------------- #
def test_read_sources(tmp_path: Path) -> None:
    from vidar.batch import read_sources

    listing = tmp_path / "urls.txt"
    listing.write_text(
        "# 我的清单\nBV1aa411c7mD\n\nBV1bb411c7mD\nBV1aa411c7mD\n  BV1cc411c7mD  \n",
        encoding="utf-8",
    )
    assert read_sources(listing) == ["BV1aa411c7mD", "BV1bb411c7mD", "BV1cc411c7mD"]

    # Windows 记事本/PowerShell 的 BOM 清单也要能正确解析
    bom_listing = tmp_path / "bom.txt"
    bom_listing.write_bytes("\ufeffBV1aa411c7mD\nBV1bb411c7mD\n".encode("utf-8"))
    assert read_sources(bom_listing) == ["BV1aa411c7mD", "BV1bb411c7mD"]

    empty = tmp_path / "empty.txt"
    empty.write_text("# 只有注释\n", encoding="utf-8")
    try:
        read_sources(empty)
    except VidarError as exc:
        assert "没有有效链接" in exc.message
    else:  # pragma: no cover
        raise AssertionError("空清单应报错")


def test_run_batch_continues_after_failure(monkeypatch, tmp_path: Path) -> None:  # noqa: ANN001
    from vidar.batch import run_batch

    monkeypatch.chdir(tmp_path)
    calls: list[str] = []

    class _FakeRunner:
        def __init__(self, ctx, steps) -> None:  # noqa: ANN001
            self.ctx = ctx

        def run(self, *, from_step=None, to_step=None, dry_run=False) -> None:  # noqa: ANN001
            calls.append(self.ctx.source)
            if "bad" in self.ctx.source:
                raise VidarError("模拟失败")

    monkeypatch.setattr("vidar.pipeline.PipelineRunner", _FakeRunner)

    events: list[tuple[str, dict]] = []
    settings = Settings()
    result = run_batch(
        settings,
        ["BV1aa411c7mD", "bad-source", "BV1bb411c7mD"],
        on_event=lambda event, data: events.append((event, data)),
    )

    assert calls == ["BV1aa411c7mD", "bad-source", "BV1bb411c7mD"]  # 失败不中断
    assert [item.status for item in result.items] == ["done", "failed", "done"]
    assert result.done_count == 2 and len(result.failed) == 1
    assert events[0][0] == "batch_start" and events[-1][0] == "batch_finished"
    assert any(event == "batch_item" and data.get("status") == "failed" for event, data in events)


def test_run_batch_stop_on_error(monkeypatch, tmp_path: Path) -> None:  # noqa: ANN001
    from vidar.batch import run_batch

    monkeypatch.chdir(tmp_path)

    class _FakeRunner:
        def __init__(self, ctx, steps) -> None:  # noqa: ANN001
            self.ctx = ctx

        def run(self, *, from_step=None, to_step=None, dry_run=False) -> None:  # noqa: ANN001
            if "bad" in self.ctx.source:
                raise VidarError("模拟失败")

    monkeypatch.setattr("vidar.pipeline.PipelineRunner", _FakeRunner)
    result = run_batch(Settings(), ["bad-source", "BV1bb411c7mD"], stop_on_error=True)
    assert [item.status for item in result.items] == ["failed", "pending"]

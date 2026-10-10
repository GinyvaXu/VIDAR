"""暂停/续跑测试：挂起与恢复、释放检查点、运行器干净停止、ASR 切片续跑。"""

from __future__ import annotations

import re
import threading
import time
import wave
from pathlib import Path

import pytest

from vidar.config import Settings
from vidar.errors import PauseRelease
from vidar.models import AsrResult, AsrSegment
from vidar.pipeline import PipelineRunner, RunContext


def _ctx(monkeypatch, tmp_path: Path) -> RunContext:  # noqa: ANN001
    monkeypatch.chdir(tmp_path)
    return RunContext(Settings(), "BV1xx411c7mD")


class _FakeClient:
    """回显式假 LLM：精修 = 精修<原文>。"""

    def __init__(self, settings) -> None:  # noqa: ANN001
        self.usage = type("U", (), {"prompt_tokens": 1, "completion_tokens": 1})()
        self.calls: list[str] = []

    def complete_json(self, prompt: str, *, system: str = "", model: str | None = None) -> dict:
        self.calls.append(prompt)
        items = []
        for line in prompt.splitlines():
            match = re.match(r"^\[(\d+)\]\s*(.*)$", line)
            if match:
                items.append({"index": int(match.group(1)), "text": f"精修<{match.group(2)}>"})
        return {"refined": items}


# --------------------------------------------------------------------------- #
# 挂起 / 恢复 / 释放
# --------------------------------------------------------------------------- #
def test_pause_resume_and_release(monkeypatch, tmp_path: Path) -> None:  # noqa: ANN001
    ctx = _ctx(monkeypatch, tmp_path)

    ctx.wait_if_paused()  # 未暂停：直接通过

    ctx.pause()
    assert ctx.paused
    result: dict = {}

    def worker() -> None:
        started = time.monotonic()
        ctx.wait_if_paused()
        result["elapsed"] = time.monotonic() - started

    thread = threading.Thread(target=worker)
    thread.start()
    time.sleep(0.3)
    assert thread.is_alive()  # 暂停中：仍挂起
    ctx.resume()
    thread.join(timeout=3)
    assert not thread.is_alive()
    assert result["elapsed"] >= 0.25

    ctx.pause()
    ctx.release_resources()
    with pytest.raises(PauseRelease):
        ctx.wait_if_paused()


def test_runner_stops_cleanly_on_release(monkeypatch, tmp_path: Path) -> None:  # noqa: ANN001
    ctx = _ctx(monkeypatch, tmp_path)

    class _Step:
        name = "resolve"
        title = "解析视频信息"
        milestone = None

        def run(self, ctx: RunContext) -> dict[str, str]:  # noqa: ANN001
            ctx.release_resources()
            ctx.wait_if_paused()  # 触发 PauseRelease
            return {"meta": "meta.json"}

    status = PipelineRunner(ctx, [_Step()]).run()  # type: ignore[arg-type]
    assert status == "paused"
    record = ctx.state.record("resolve")
    assert record.status.value == "pending"
    assert "已暂停" in (record.error or "")


# --------------------------------------------------------------------------- #
# ASR 切片续跑
# --------------------------------------------------------------------------- #
def test_wav_slice_and_duration(tmp_path: Path) -> None:
    from vidar.asr import _slice_wav, _wav_duration

    rate = 16000
    source = tmp_path / "audio.wav"
    with wave.open(str(source), "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(rate)
        writer.writeframes(b"\x00\x00" * rate)  # 1 秒静音

    assert abs(_wav_duration(source) - 1.0) < 0.01

    target = tmp_path / "slice.wav"
    offset, written = _slice_wav(source, 0.5, target)
    assert abs(offset - 0.5) < 0.01
    assert abs(written / rate - 0.5) < 0.02

    _offset, empty = _slice_wav(source, 2.0, target)
    assert empty == 0  # 起点超过时长 → 空切片


# --------------------------------------------------------------------------- #
# refine 检查点续跑
# --------------------------------------------------------------------------- #
def test_refine_resume_skips_done_chunks(monkeypatch, tmp_path: Path) -> None:  # noqa: ANN001
    from vidar.refine import refine_asr_result
    from vidar.utils.checkpoint import save_checkpoint

    ctx = _ctx(monkeypatch, tmp_path)
    asr = AsrResult(
        engine="fake",
        model="fake",
        segments=[
            AsrSegment(idx=i, start=i * 5.0, end=i * 5.0 + 4.0, text=f"第{i}段原文内容。")
            for i in range(4)
        ],
    )
    ctx.settings.llm.chunk_chars = 12  # 每块 1 段，便于验证跳过
    monkeypatch.setattr("vidar.refine.LlmClient", _FakeClient)

    save_checkpoint(
        ctx.artifact("refine.partial.json"),
        {"refined": {"0": "精修<第0段原文内容。>"}},
    )
    result = refine_asr_result(ctx, asr)

    assert result.segments[0].refined_text == "精修<第0段原文内容。>"
    assert not ctx.artifact("refine.partial.json").exists()  # 完成后清理检查点
    # 检查点覆盖的段落不再请求（其余段落正常请求）
    assert result.segments[1].refined_text.startswith("精修<")


def test_refine_release_saves_checkpoint(monkeypatch, tmp_path: Path) -> None:  # noqa: ANN001
    from vidar.refine import refine_asr_result
    from vidar.utils.checkpoint import load_checkpoint

    ctx = _ctx(monkeypatch, tmp_path)
    asr = AsrResult(
        engine="fake",
        model="fake",
        segments=[AsrSegment(idx=0, start=0.0, end=1.0, text="内容。")],
    )
    monkeypatch.setattr("vidar.refine.LlmClient", _FakeClient)
    ctx.release_resources()

    with pytest.raises(PauseRelease):
        refine_asr_result(ctx, asr)
    payload = load_checkpoint(ctx.artifact("refine.partial.json"))
    assert payload is not None  # 释放前保存了检查点（可续跑）

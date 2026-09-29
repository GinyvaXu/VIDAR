"""状态机测试：断点续跑、产物校验、崩溃恢复。"""

from __future__ import annotations

from pathlib import Path

from vidar.models import StepStatus
from vidar.pipeline.state import StateStore

ORDER = ["a", "b", "c"]


def _store(tmp_path: Path) -> StateStore:
    return StateStore(tmp_path / "state.json", "BVtest")


def _write_artifact(tmp_path: Path, name: str) -> dict[str, str]:
    (tmp_path / f"{name}.json").write_text("{}", encoding="utf-8")
    return {"data": f"{name}.json"}


def test_roundtrip_and_done(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.mark_running("a")
    store.mark_done("a", _write_artifact(tmp_path, "a"))
    assert store.is_done("a")

    reloaded = StateStore(tmp_path / "state.json", "BVtest")
    assert reloaded.status("a") is StepStatus.DONE
    assert reloaded.is_done("a") is True


def test_missing_artifact_not_done(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.mark_done("a", {"data": "missing.json"})
    assert store.status("a") is StepStatus.DONE
    assert store.is_done("a") is False


def test_running_reset_on_load(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.mark_running("a")
    reloaded = StateStore(tmp_path / "state.json", "BVtest")
    assert reloaded.status("a") is StepStatus.PENDING


def test_reset_from(tmp_path: Path) -> None:
    store = _store(tmp_path)
    for name in ORDER:
        store.mark_done(name, _write_artifact(tmp_path, name))

    reset = store.reset_from("b", ORDER)
    assert reset == ["b", "c"]
    assert store.is_done("a") is True
    assert store.status("b") is StepStatus.PENDING
    assert store.status("c") is StepStatus.PENDING
    assert store.record("b").artifacts == {}


def test_failed_records_error(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.mark_failed("a", "boom")
    record = store.record("a")
    assert record.status is StepStatus.FAILED
    assert record.error == "boom"

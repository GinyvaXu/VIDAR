"""步骤状态机：state.json 的读写与断点续跑语义。"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, Field

from ..models import StepStatus


def _now() -> datetime:
    return datetime.now()


class StepRecord(BaseModel):
    status: StepStatus = StepStatus.PENDING
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error: str | None = None
    artifacts: dict[str, str] = Field(default_factory=dict)


class RunState(BaseModel):
    schema_version: int = 1
    key: str
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)
    steps: dict[str, StepRecord] = Field(default_factory=dict)


class StateStore:
    """state.json 管理器。

    - 加载时把遗留的 running 重置为 pending（视为进程崩溃）。
    - done 的步骤若产物文件缺失，视为需要重跑。
    """

    def __init__(self, path: Path, key: str) -> None:
        self.path = path
        self.key = key
        self.work_dir = path.parent
        self.state = self._load()

    # ------------------------------------------------------------------ #
    def _load(self) -> RunState:
        if self.path.exists():
            try:
                state = RunState.model_validate_json(self.path.read_text(encoding="utf-8"))
                for record in state.steps.values():
                    if record.status is StepStatus.RUNNING:
                        record.status = StepStatus.PENDING
                # key 以目录为准（URL 哈希等情况可能变化）
                state.key = self.key
                return state
            except (ValueError, OSError):
                pass  # 损坏则重建
        return RunState(key=self.key)

    def save(self) -> None:
        self.state.updated_at = _now()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            self.state.model_dump_json(indent=2),
            encoding="utf-8",
        )

    # ------------------------------------------------------------------ #
    def record(self, step: str) -> StepRecord:
        return self.state.steps.setdefault(step, StepRecord())

    def status(self, step: str) -> StepStatus:
        return self.record(step).status

    def is_done(self, step: str) -> bool:
        record = self.record(step)
        if record.status is not StepStatus.DONE:
            return False
        # 产物缺失 → 需要重跑
        return all((self.work_dir / rel).exists() for rel in record.artifacts.values())

    def mark_running(self, step: str) -> None:
        record = self.record(step)
        record.status = StepStatus.RUNNING
        record.started_at = _now()
        record.finished_at = None
        record.error = None
        self.save()

    def mark_done(self, step: str, artifacts: dict[str, str] | None = None) -> None:
        record = self.record(step)
        record.status = StepStatus.DONE
        record.finished_at = _now()
        record.error = None
        if artifacts:
            record.artifacts.update(artifacts)
        self.save()

    def mark_failed(self, step: str, error: str) -> None:
        record = self.record(step)
        record.status = StepStatus.FAILED
        record.finished_at = _now()
        record.error = error
        self.save()

    def mark_pending(self, step: str, note: str = "") -> None:
        """回退为待执行（用户取消 / 需要重跑）。"""
        record = self.record(step)
        record.status = StepStatus.PENDING
        record.finished_at = _now()
        record.error = note or None
        self.save()

    def reset_from(self, step: str, ordered_steps: list[str]) -> list[str]:
        """把 step 及其后续步骤全部重置为 pending，返回被重置的步骤名。"""
        if step not in ordered_steps:
            return []
        index = ordered_steps.index(step)
        reset: list[str] = []
        for name in ordered_steps[index:]:
            record = self.record(name)
            if record.status is not StepStatus.PENDING:
                record.status = StepStatus.PENDING
                record.error = None
                record.artifacts = {}
                reset.append(name)
        self.save()
        return reset

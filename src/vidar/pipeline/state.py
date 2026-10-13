"""步骤状态机：state.json 的读写与断点续跑语义。"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, Field

from ..models import StepStatus

# 各步骤的按块检查点文件（重置步骤时一并清理，避免旧检查点被复用）
_STEP_PARTIALS: dict[str, str] = {
    "refine": "refine.partial.json",
    "ads": "ads.partial.json",
    "translate": "translate.partial.json",
    "summarize": "summarize.partial.json",
}


def _now() -> datetime:
    return datetime.now()


class StepRecord(BaseModel):
    status: StepStatus = StepStatus.PENDING
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error: str | None = None
    artifacts: dict[str, str] = Field(default_factory=dict)
    fingerprint: str | None = None  # 影响产物的配置指纹（变化 → 自动重跑）


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
        # 原子写：临时文件 + os.replace，避免断电/被杀时留下半截 state.json
        payload = self.state.model_dump_json(indent=2)
        tmp_path = self.path.with_suffix(".json.tmp")
        tmp_path.write_text(payload, encoding="utf-8")
        os.replace(tmp_path, self.path)

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

    def mark_done(
        self,
        step: str,
        artifacts: dict[str, str] | None = None,
        *,
        fingerprint: str | None = None,
    ) -> None:
        record = self.record(step)
        record.status = StepStatus.DONE
        record.finished_at = _now()
        record.error = None
        if artifacts:
            record.artifacts.update(artifacts)
        record.fingerprint = fingerprint
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
                record.fingerprint = None
                reset.append(name)
            partial = _STEP_PARTIALS.get(name)
            if partial:
                (self.work_dir / partial).unlink(missing_ok=True)
        self.save()
        return reset

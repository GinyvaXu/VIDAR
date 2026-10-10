"""步骤检查点：暂停/释放资源时保存中间进度，续跑时跳过已完成部分。

约定：每个步骤用 `work/<key>/<step>.partial.json` 保存自己的进度；
- 正常完成后必须 `clear_checkpoint`；
- 取消（CancelledError）保留检查点；暂停释放（PauseRelease）保留检查点；
- 续跑时 `load_checkpoint` 读出进度并跳过已完成部分。
"""

from __future__ import annotations

import json
from pathlib import Path


def load_checkpoint(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None
    return payload if isinstance(payload, dict) else None


def save_checkpoint(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def clear_checkpoint(path: Path) -> None:
    path.unlink(missing_ok=True)


__all__ = ["clear_checkpoint", "load_checkpoint", "save_checkpoint"]

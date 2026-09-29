"""RunContext：一次运行的全部上下文（配置、路径、状态、取消与事件）。"""

from __future__ import annotations

import contextlib
import hashlib
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..config import Settings
from ..errors import CancelledError
from ..logging_setup import get_logger
from ..models import VideoMeta
from ..utils.text import parse_bvid
from .state import StateStore


def resolve_path(value: Path, base: Path | None = None) -> Path:
    value = Path(value)
    if value.is_absolute():
        return value
    return (base or Path.cwd()) / value


def make_key(source: str) -> str:
    bvid = parse_bvid(source)
    if bvid:
        return bvid
    return "src-" + hashlib.sha1(source.encode("utf-8")).hexdigest()[:10]


class RunContext:
    def __init__(self, settings: Settings, source: str) -> None:
        self.settings = settings
        self.source = source.strip()
        self.key = make_key(self.source)
        self.log = get_logger(self.key)

        self.work_dir = resolve_path(settings.paths.work_dir) / self.key
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.output_root = resolve_path(settings.paths.output_dir)

        self.state = StateStore(self.work_dir / "state.json", self.key)
        self.meta: VideoMeta | None = self._load_meta()

        # 取消与事件（GUI / CLI 进度展示用）
        self.cancel_event = threading.Event()
        self.on_event: Callable[[str, dict[str, Any]], None] | None = None

    # ------------------------------------------------------------------ #
    def emit(self, event: str, **data: Any) -> None:
        """向订阅者广播事件；回调异常不影响主流程。"""
        if self.on_event is not None:
            with contextlib.suppress(Exception):  # 回调问题不应影响任务
                self.on_event(event, data)

    def cancel(self) -> None:
        self.cancel_event.set()
        self.emit("cancelled")

    def raise_if_cancelled(self) -> None:
        if self.cancel_event.is_set():
            raise CancelledError()

    # ------------------------------------------------------------------ #
    def artifact(self, name: str) -> Path:
        return self.work_dir / name

    def _load_meta(self) -> VideoMeta | None:
        path = self.artifact("meta.json")
        if path.exists():
            try:
                return VideoMeta.model_validate_json(path.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                return None
        return None

    def save_meta(self, meta: VideoMeta) -> None:
        self.meta = meta
        self.artifact("meta.json").write_text(meta.model_dump_json(indent=2), encoding="utf-8")

    def final_output_dir(self, title: str, pubdate: str | None = None) -> Path:
        from ..utils.text import slugify

        date_part = (pubdate or "").replace("-", "")[:8] or "00000000"
        folder = f"{date_part}_{self.key}_{slugify(title)}"
        return self.output_root / folder

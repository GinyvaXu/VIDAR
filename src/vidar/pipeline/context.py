"""RunContext：一次运行的全部上下文（配置、路径、状态、取消与事件）。"""

from __future__ import annotations

import contextlib
import hashlib
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..config import Settings
from ..errors import CancelledError, PauseRelease
from ..logging_setup import get_logger
from ..models import VideoMeta
from ..source import extract_page
from ..utils.text import parse_bvid
from .state import StateStore


def resolve_path(value: Path, base: Path | None = None) -> Path:
    value = Path(value)
    if value.is_absolute():
        return value
    if base is None:
        # 便携包（frozen）：相对路径锚定到 exe 目录，避免从其他目录启动时写错位置
        from ..config import exe_dir

        base = exe_dir() or Path.cwd()
    return base / value


def make_key(source: str) -> str:
    bvid = parse_bvid(source)
    if bvid:
        # 分 P 视频用独立任务目录：BV1xx-p2（P1 保持旧键，兼容历史任务）
        page = extract_page(source)
        return f"{bvid}-p{page}" if page > 1 else bvid
    return "src-" + hashlib.sha1(source.encode("utf-8")).hexdigest()[:10]


class RunContext:
    def __init__(self, settings: Settings, source: str, *, key: str | None = None) -> None:
        self.settings = settings
        self.source = source.strip()
        self.key = key or make_key(self.source)
        self.log = get_logger(self.key)

        self.work_dir = resolve_path(settings.paths.work_dir) / self.key
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.output_root = resolve_path(settings.paths.output_dir)

        self.state = StateStore(self.work_dir / "state.json", self.key)
        self.meta: VideoMeta | None = self._load_meta()

        # 取消与事件（GUI / CLI 进度展示用）
        self.cancel_event = threading.Event()
        self.pause_event = threading.Event()  # set = 已暂停
        self.release_event = threading.Event()  # set = 请求释放资源（配合暂停）
        self.on_event: Callable[[str, dict[str, Any]], None] | None = None

    # ------------------------------------------------------------------ #
    def emit(self, event: str, **data: Any) -> None:
        """向订阅者广播事件；回调异常不影响主流程。"""
        if self.on_event is not None:
            with contextlib.suppress(Exception):  # 回调问题不应影响任务
                self.on_event(event, data)

    def cancel(self) -> None:
        self.cancel_event.set()
        self.pause_event.clear()  # 取消时唤醒暂停等待
        self.emit("cancelled")

    def raise_if_cancelled(self) -> None:
        if self.cancel_event.is_set():
            raise CancelledError()

    # ---- 暂停 / 继续 / 释放资源 ----
    def pause(self) -> None:
        """请求暂停：在当前检查点（段/请求块边界）挂起。"""
        if self.cancel_event.is_set():
            return
        self.pause_event.set()
        self.emit("paused")

    def resume(self) -> None:
        """继续运行。"""
        self.release_event.clear()
        self.pause_event.clear()
        self.emit("resumed")

    def release_resources(self) -> None:
        """暂停状态下释放资源（模型/显存）：当前步骤从检查点退出，标记为可续跑。"""
        self.release_event.set()
        self.pause_event.clear()  # 唤醒等待线程，统一走释放分支

    @property
    def paused(self) -> bool:
        return self.pause_event.is_set()

    def wait_if_paused(self) -> None:
        """检查点：暂停时挂起；请求释放时抛 PauseRelease（由调用方保存检查点后向上传播）。"""
        while self.pause_event.is_set():
            if self.cancel_event.is_set():
                raise CancelledError()
            time.sleep(0.1)
        if self.release_event.is_set():
            raise PauseRelease()

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

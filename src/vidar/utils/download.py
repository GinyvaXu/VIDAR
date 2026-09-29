"""通用文件下载：分片并行、断点续传、进度聚合（模型 / CUDA wheel 共用）。"""

from __future__ import annotations

import shutil
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from ..errors import VidarError

CHUNK_THRESHOLD = 200 * 1024 * 1024  # 超过 200MB 启用分片
CHUNK_SIZE = 32 * 1024 * 1024  # 每片 32MB

ProgressCallback = Callable[[str, int, int], None]
CancelCheck = Callable[[], bool]


@dataclass
class Progress:
    """线程安全的聚合进度。"""

    total: int = 0
    done: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock)

    def reset(self) -> None:
        with self.lock:
            self.total = 0
            self.done = 0

    def add_total(self, amount: int) -> None:
        with self.lock:
            self.total += amount

    def add_done(self, amount: int) -> None:
        with self.lock:
            self.done += amount

    def snapshot(self) -> tuple[int, int]:
        with self.lock:
            return self.done, self.total


def _plan_parts(size: int) -> list[tuple[int, int]]:
    parts: list[tuple[int, int]] = []
    start = 0
    while start < size:
        end = min(start + CHUNK_SIZE - 1, size - 1)
        parts.append((start, end))
        start = end + 1
    return parts


def _abort_if_cancelled(should_cancel: CancelCheck | None) -> None:
    if should_cancel is not None and should_cancel():
        raise VidarError("下载已取消（分片已保留，重跑可续传）")


def _download_file_simple(
    url: str,
    target: Path,
    size: int,
    description: str,
    progress: Progress,
    on_progress: ProgressCallback | None,
    should_cancel: CancelCheck | None,
) -> None:
    temp = target.with_suffix(target.suffix + ".tmp")
    temp.unlink(missing_ok=True)
    progress.add_total(size)
    with (
        httpx.Client(timeout=httpx.Timeout(30.0, read=120.0), follow_redirects=True) as client,
        client.stream("GET", url) as response,
    ):
        response.raise_for_status()
        with temp.open("wb") as handle:
            for chunk in response.iter_bytes(1024 * 1024):
                _abort_if_cancelled(should_cancel)
                handle.write(chunk)
                progress.add_done(len(chunk))
                if on_progress:
                    done, total = progress.snapshot()
                    on_progress(description, done, total)
    temp.replace(target)


def _download_file_parallel(
    url: str,
    target: Path,
    size: int,
    description: str,
    connections: int,
    progress: Progress,
    on_progress: ProgressCallback | None,
    should_cancel: CancelCheck | None,
) -> None:
    parts = [
        (target.with_suffix(target.suffix + f".part{index:03d}"), start, end)
        for index, (start, end) in enumerate(_plan_parts(size))
    ]
    remaining: list[tuple[Path, int, int]] = []
    finished_bytes = 0
    for part, start, end in parts:
        span = end - start + 1
        if part.exists() and part.stat().st_size == span:
            finished_bytes += span
        else:
            part.unlink(missing_ok=True)  # 残片重下（单片 ≤32MB，成本可控）
            remaining.append((part, start, end))
    progress.add_total(size - finished_bytes)

    def worker(item: tuple[Path, int, int]) -> None:
        part, start, end = item
        headers = {"Range": f"bytes={start}-{end}"}
        with (
            httpx.Client(timeout=httpx.Timeout(30.0, read=120.0), follow_redirects=True) as client,
            client.stream("GET", url, headers=headers) as response,
        ):
            response.raise_for_status()
            with part.open("wb") as handle:
                for chunk in response.iter_bytes(1024 * 1024):
                    _abort_if_cancelled(should_cancel)
                    handle.write(chunk)
                    progress.add_done(len(chunk))
                    if on_progress:
                        done, total = progress.snapshot()
                        on_progress(description, done, total)

    if remaining:
        with ThreadPoolExecutor(max_workers=max(1, connections)) as pool:
            futures = [pool.submit(worker, item) for item in remaining]
            for future in as_completed(futures):
                future.result()

    with target.open("wb") as out:
        for part, _start, _end in parts:
            with part.open("rb") as source:
                shutil.copyfileobj(source, out, length=4 * 1024 * 1024)
            part.unlink(missing_ok=True)


def download_file(
    url: str,
    target: Path,
    size: int,
    *,
    description: str = "",
    connections: int = 6,
    progress: Progress | None = None,
    on_progress: ProgressCallback | None = None,
    should_cancel: CancelCheck | None = None,
) -> None:
    """下载单个文件（自动选择直连/分片并行），支持断点续传。"""
    target.parent.mkdir(parents=True, exist_ok=True)
    progress = progress or Progress()
    label = description or target.name
    if size >= CHUNK_THRESHOLD:
        _download_file_parallel(
            url, target, size, label, connections, progress, on_progress, should_cancel
        )
    else:
        _download_file_simple(url, target, size, label, progress, on_progress, should_cancel)

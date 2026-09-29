"""ASR 模型辅助下载与校验（hf-mirror / HuggingFace 官方源）。

特性：
- 大文件（model.bin）分片并行下载，小文件直连
- 断点续传：分片文件保留，重跑跳过已完成分片
- 大小校验：在线取 HF API 元数据，离线用内置表兜底
"""

from __future__ import annotations

import shutil
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from .errors import VidarError

ENDPOINTS: dict[str, str] = {
    "hf-mirror": "https://hf-mirror.com",
    "huggingface": "https://huggingface.co",
}

# 常用别名 → HuggingFace 仓库
REPO_ALIASES: dict[str, str] = {
    "large-v3": "Systran/faster-whisper-large-v3",
    "large-v3-turbo": "Systran/faster-whisper-large-v3-turbo",
    "medium": "Systran/faster-whisper-medium",
    "small": "Systran/faster-whisper-small",
    "base": "Systran/faster-whisper-base",
    "tiny": "Systran/faster-whisper-tiny",
}

REQUIRED_FILES: tuple[str, ...] = (
    "model.bin",
    "config.json",
    "tokenizer.json",
    "vocabulary.json",
    "preprocessor_config.json",
)

# 离线兜底：已知模型各文件大小（字节），以本机实测为准
KNOWN_SIZES: dict[str, dict[str, int]] = {
    "Systran/faster-whisper-large-v3": {
        "model.bin": 3_087_284_237,
        "config.json": 2_394,
        "preprocessor_config.json": 340,
        "tokenizer.json": 2_480_617,
        "vocabulary.json": 1_068_114,
    },
}

CHUNK_THRESHOLD = 200 * 1024 * 1024  # 超过 200MB 启用分片
CHUNK_SIZE = 32 * 1024 * 1024  # 每片 32MB
BIG_FILE_MIN_BYTES = 100 * 1024 * 1024  # 未知模型时 model.bin 的合理下限


def resolve_repo(model: str) -> str:
    """把别名 / 包名 / 仓库 id 统一成 HF 仓库 id。"""
    if model in REPO_ALIASES:
        return REPO_ALIASES[model]
    if "/" in model:
        return model
    if model.startswith("faster-whisper-"):
        return f"Systran/{model}"
    return model


def default_model_dir(model: str, base: Path | None = None) -> Path:
    name = resolve_repo(model).split("/")[-1]
    return (base or Path.cwd()) / "models" / name


def expected_sizes(repo: str, *, endpoint: str | None = None) -> dict[str, int] | None:
    """优先在线取元数据，失败回退内置表（可能为 None）。"""
    if endpoint:
        with suppress(Exception), httpx.Client(timeout=15, follow_redirects=True) as client:
            response = client.get(f"{endpoint}/api/models/{repo}")
            response.raise_for_status()
            siblings = response.json().get("siblings") or []
            sizes = {
                str(item["rfilename"]): int(item["size"])
                for item in siblings
                if isinstance(item, dict)
                and item.get("rfilename")
                and isinstance(item.get("size"), int)
            }
            if sizes:
                return sizes
    return KNOWN_SIZES.get(repo)


@dataclass(frozen=True)
class CheckResult:
    name: str
    expected: int | None
    actual: int | None
    ok: bool


def check_model(model: str, model_dir: Path, *, endpoint: str | None = None) -> list[CheckResult]:
    """校验模型文件完整性（存在性 + 大小比对；无元数据时做合理性检查）。"""
    repo = resolve_repo(model)
    sizes = expected_sizes(repo, endpoint=endpoint) or {}
    results: list[CheckResult] = []
    for name in REQUIRED_FILES:
        path = model_dir / name
        actual = path.stat().st_size if path.exists() else None
        expected = sizes.get(name)
        if actual is None:
            ok = False
        elif expected is not None:
            ok = actual == expected
        elif name == "model.bin":
            ok = actual > BIG_FILE_MIN_BYTES
        else:
            ok = actual > 0
        results.append(CheckResult(name=name, expected=expected, actual=actual, ok=ok))
    return results


@dataclass
class _Progress:
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


ProgressCallback = Callable[[str, int, int], None]
CancelCheck = Callable[[], bool]


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
    progress: _Progress,
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
    progress: _Progress,
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
    progress.add_done(0)

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


def download_model(
    model: str,
    model_dir: Path,
    *,
    source: str = "hf-mirror",
    connections: int = 6,
    on_progress: ProgressCallback | None = None,
    should_cancel: CancelCheck | None = None,
) -> tuple[list[str], list[str]]:
    """下载模型到 model_dir，返回 (已下载, 已跳过)。"""
    endpoint = ENDPOINTS.get(source, source)
    repo = resolve_repo(model)
    model_dir.mkdir(parents=True, exist_ok=True)

    sizes = expected_sizes(repo, endpoint=endpoint)
    if not sizes:
        raise VidarError(
            f"无法获取模型元数据：{repo}",
            hint="检查网络，或改用 --source huggingface；已知模型可用 large-v3/medium/small",
        )
    missing = [name for name in REQUIRED_FILES if name not in sizes]
    if missing:
        raise VidarError(f"仓库 {repo} 缺少必需文件：{', '.join(missing)}")

    progress = _Progress()
    progress.reset()
    downloaded: list[str] = []
    skipped: list[str] = []

    for name in REQUIRED_FILES:
        size = sizes[name]
        target = model_dir / name
        if target.exists() and target.stat().st_size == size:
            skipped.append(name)
            continue
        url = f"{endpoint}/{repo}/resolve/main/{name}"
        if size >= CHUNK_THRESHOLD:
            _download_file_parallel(
                url, target, size, name, connections, progress, on_progress, should_cancel
            )
        else:
            _download_file_simple(url, target, size, name, progress, on_progress, should_cancel)
        if target.stat().st_size != size:
            raise VidarError(f"{name} 下载后大小不符（期望 {size}，实际 {target.stat().st_size}）")
        downloaded.append(name)

    return downloaded, skipped


def ensure_model_available(model: str, model_dir: Path, *, source: str = "hf-mirror") -> None:
    """下载 + 校验一步到位，校验失败抛错。"""
    download_model(model, model_dir, source=source)
    results = check_model(model, model_dir)
    bad = [item.name for item in results if not item.ok]
    if bad:
        raise VidarError(f"模型校验未通过：{', '.join(bad)}")

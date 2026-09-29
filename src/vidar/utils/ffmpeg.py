"""ffmpeg / ffprobe 封装（可选组件）。

音频解码已内置 PyAV（faster-whisper 依赖），ffmpeg 不是必需项：
仅当用户在 config.toml 配置了路径、或系统 PATH 中存在时才被使用（探测等扩展能力）。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

from ..config import PathsSettings

_CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def _run(cmd: list[str], *, timeout: int | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        creationflags=_CREATE_NO_WINDOW,
    )


def find_executable(configured: str | None, name: str) -> str | None:
    if configured:
        candidate = Path(configured)
        if candidate.exists():
            return str(candidate)
    return shutil.which(name)


def find_ffmpeg(paths: PathsSettings) -> str | None:
    return find_executable(paths.ffmpeg, "ffmpeg")


def find_ffprobe(paths: PathsSettings) -> str | None:
    return find_executable(paths.ffprobe, "ffprobe")


def ffmpeg_version(exe: str) -> str | None:
    try:
        result = _run([exe, "-version"], timeout=20)
    except OSError:
        return None
    if result.returncode != 0 or not result.stdout:
        return None
    return result.stdout.splitlines()[0].strip()


def probe_duration(ffprobe: str, media: Path) -> float | None:
    result = _run(
        [
            ffprobe,
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "json",
            str(media),
        ],
        timeout=60,
    )
    if result.returncode != 0:
        return None
    try:
        payload = json.loads(result.stdout or "{}")
        value = payload.get("format", {}).get("duration")
        return float(value) if value is not None else None
    except (ValueError, TypeError):
        return None

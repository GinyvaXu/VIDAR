"""ffmpeg / ffprobe 封装。"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

from ..config import PathsSettings
from ..errors import CancelledError, VidarError

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
    """查找 ffmpeg：显式配置 > PATH > imageio-ffmpeg 自带静态版。"""
    found = find_executable(paths.ffmpeg, "ffmpeg")
    if found:
        return found
    return _bundled_ffmpeg()


def _bundled_ffmpeg() -> str | None:
    """回退：imageio-ffmpeg 附带的静态 ffmpeg（随 asr 可选组安装）。"""
    try:
        import imageio_ffmpeg  # type: ignore[import-not-found]
    except ImportError:
        return None
    try:
        exe = imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:  # noqa: BLE001 - 包内部异常类型不稳定
        return None
    return exe if exe and Path(exe).exists() else None


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


def extract_audio(
    ffmpeg: str,
    src: Path,
    dst: Path,
    *,
    should_cancel: Callable[[], bool] | None = None,
) -> None:
    """抽取 16kHz 单声道 PCM WAV（faster-whisper 的标准输入），支持中途取消。"""
    dst.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        ffmpeg, "-y",
        "-i", str(src),
        "-vn",
        "-ac", "1",
        "-ar", "16000",
        "-c:a", "pcm_s16le",
        str(dst),
    ]
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=_CREATE_NO_WINDOW,
    )
    stderr = ""
    while True:
        try:
            _, stderr = proc.communicate(timeout=1.0)
            break
        except subprocess.TimeoutExpired:
            if should_cancel is not None and should_cancel():
                proc.kill()
                proc.communicate()
                raise CancelledError("音频转码已取消") from None
    if proc.returncode != 0:
        tail = "\n".join((stderr or "").splitlines()[-8:])
        raise VidarError(f"ffmpeg 抽取音频失败：{dst.name}", hint=tail)

"""音频解码：任意格式 → 16kHz 单声道 PCM WAV（基于 PyAV，无需外部 ffmpeg）。

依赖通过函数内延迟导入，核心安装（无 asr 可选组）不受影响。
"""

from __future__ import annotations

import wave
from collections.abc import Callable
from pathlib import Path

from ..errors import CancelledError, DependencyError

SAMPLE_RATE = 16000
SAMPLE_WIDTH = 2  # 16-bit PCM


def decode_to_wav(src: Path, dst: Path, *, should_cancel: Callable[[], bool] | None = None) -> None:
    """把任意音视频文件解码为 16kHz 单声道 16-bit PCM WAV。"""
    if should_cancel is not None and should_cancel():
        raise CancelledError("音频解码已取消")
    try:
        import numpy as np
        from faster_whisper.audio import decode_audio
    except ImportError as exc:  # pragma: no cover - 缺依赖时给出指引
        raise DependencyError(
            "缺少音频解码依赖（faster-whisper / numpy）",
            hint="执行 uv sync --extra asr 安装",
        ) from exc

    samples = decode_audio(str(src), sampling_rate=SAMPLE_RATE)  # float32 单声道
    if should_cancel is not None and should_cancel():
        raise CancelledError("音频解码已取消")

    pcm = np.clip(samples * 32767.0, -32768.0, 32767.0).astype("<i2")
    dst.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(dst), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(SAMPLE_WIDTH)
        handle.setframerate(SAMPLE_RATE)
        handle.writeframes(pcm.tobytes())

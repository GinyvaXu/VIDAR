"""音频解码测试（PyAV 路径，不依赖 ffmpeg）。

CI 只装核心依赖时会自动跳过（importorskip）。
"""

from __future__ import annotations

import wave
from pathlib import Path

import pytest

pytest.importorskip("numpy")
pytest.importorskip("faster_whisper")

from vidar.utils.audio import decode_to_wav  # noqa: E402


def _make_wav(path: Path, *, seconds: float = 1.0, rate: int = 22050) -> None:
    import numpy as np

    timeline = np.linspace(0, seconds, int(rate * seconds), endpoint=False)
    tone = (0.3 * np.sin(2 * np.pi * 440 * timeline) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(tone.tobytes())


def test_decode_to_wav_resamples_to_16k_mono(tmp_path: Path) -> None:
    src = tmp_path / "tone.wav"
    _make_wav(src, seconds=1.0, rate=22050)
    dst = tmp_path / "out.wav"

    decode_to_wav(src, dst)

    with wave.open(str(dst), "rb") as handle:
        assert handle.getnchannels() == 1
        assert handle.getsampwidth() == 2
        assert handle.getframerate() == 16000
        frames = handle.getnframes()
    assert abs(frames - 16000) <= 800  # 1 秒 ±5%（重采样边界）


def test_decode_to_wav_cancel(tmp_path: Path) -> None:
    from vidar.errors import CancelledError

    src = tmp_path / "tone.wav"
    _make_wav(src, seconds=0.2)
    with pytest.raises(CancelledError):
        decode_to_wav(src, tmp_path / "out.wav", should_cancel=lambda: True)

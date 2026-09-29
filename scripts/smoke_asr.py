"""ASR 冒烟测试：截取音频前 N 秒，用 small 模型快速验证识别链路。

用法：uv run python scripts/smoke_asr.py [秒数]
"""

from __future__ import annotations

import subprocess
import sys

from vidar.asr import transcribe
from vidar.config import load_settings
from vidar.pipeline.context import RunContext
from vidar.utils.ffmpeg import find_ffmpeg
from vidar.utils.text import format_clock


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    seconds = int(sys.argv[1]) if len(sys.argv) > 1 else 90
    settings = load_settings(None, {"asr": {"model": "small"}})
    ctx = RunContext(settings, "BV1xx411c7mD")

    sources = sorted(ctx.work_dir.glob("source.*"))
    if not sources:
        print("找不到已下载的音频，请先运行 download 步骤")
        return 1

    ffmpeg = find_ffmpeg(settings.paths)
    assert ffmpeg is not None
    clip = ctx.work_dir / f"test{seconds}.wav"
    subprocess.run(
        [ffmpeg, "-y", "-i", str(sources[0]), "-t", str(seconds),
         "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(clip)],
        check=True,
        capture_output=True,
    )

    ctx.on_event = lambda event, data: (  # type: ignore[assignment]
        print(f"[{event}] {data.get('message', '')}")
        if event == "progress"
        else None
    )
    result = transcribe(ctx, clip)
    print(f"\n识别完成：{len(result.segments)} 段")
    for seg in result.segments[:8]:
        print(f"[{format_clock(seg.start)}] {seg.text}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

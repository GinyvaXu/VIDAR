"""对任意音频文件做 ASR（调试用）。

用法：uv run python scripts/transcribe_file.py <音频路径> [模型]
"""

from __future__ import annotations

import sys
from pathlib import Path

from vidar.asr import transcribe
from vidar.config import load_settings
from vidar.pipeline.context import RunContext
from vidar.utils.text import format_clock


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if len(sys.argv) < 2:
        print("用法：python scripts/transcribe_file.py <音频路径> [模型]")
        return 2
    media = Path(sys.argv[1])
    if not media.exists():
        print(f"文件不存在：{media}")
        return 1
    model = sys.argv[2] if len(sys.argv) > 2 else None  # 不传则用 config.toml 配置

    settings = load_settings(None, {"asr": {"model": model}} if model else None)
    ctx = RunContext(settings, "TEST")
    ctx.on_event = lambda event, data: (  # type: ignore[assignment]
        print(f"[{event}] {data.get('message', '')}") if event == "progress" else None
    )
    result = transcribe(ctx, media)
    print(f"\n识别完成：{len(result.segments)} 段，时长 {format_clock(result.duration_sec or 0)}")
    for seg in result.segments:
        print(f"[{format_clock(seg.start)}] {seg.text}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

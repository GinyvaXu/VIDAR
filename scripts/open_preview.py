"""直接打开某条视频的「排版预览」参数窗口（跳过主界面，便于快速调排版）。

用法（在本项目根目录）:
    .venv\\Scripts\\pythonw.exe scripts\\open_preview.py BV1XWHC6wEMn
    # 或者想保留控制台输出时：
    uv run python scripts/open_preview.py BV1XWHC6wEMn

窗口内可选：风格（报刊/论文）、篇幅（全文/精简）、字号档位、要点截图密度，
点「重新渲染」秒级重出 PDF（写入该任务的输出目录），「用系统程序打开」查看。
"""

from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from vidar.config import load_settings
from vidar.gui.preview_dialog import PreviewDialog


def main() -> None:
    source = sys.argv[1] if len(sys.argv) > 1 else ""
    if not source:
        print("用法: python scripts/open_preview.py <BV号/URL>")
        raise SystemExit(2)

    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("VIDAR")
    settings = load_settings(None)
    dialog = PreviewDialog(settings, source)
    dialog.resize(1180, 840)
    dialog.show()
    app.exec()


if __name__ == "__main__":
    main()

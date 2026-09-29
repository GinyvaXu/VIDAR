"""GUI 冒烟测试：离屏构建主窗口并渲染一次，验证依赖与代码无误。

用法：uv run python scripts/gui_smoke.py
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from vidar.config import load_settings  # noqa: E402
from vidar.gui.app import MainWindow  # noqa: E402


def main() -> int:
    app = QApplication([])
    window = MainWindow(load_settings(None))
    window.show()
    app.processEvents()
    print(
        f"GUI smoke OK：窗口「{window.windowTitle()}」"
        f"，步骤 {window.steps_table.rowCount()} 行"
        f"，历史 {window.history.count()} 条"
    )
    window.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

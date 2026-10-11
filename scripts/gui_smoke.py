"""GUI 冒烟测试：离屏构建主窗口（含队列页）与各对话框，验证依赖与代码无误。

用法：uv run python scripts/gui_smoke.py
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from vidar.config import load_settings  # noqa: E402
from vidar.gui.app import MainWindow  # noqa: E402
from vidar.gui.llm_wizard import LlmWizardDialog  # noqa: E402
from vidar.gui.settings_dialog import SettingsDialog  # noqa: E402


def main() -> int:
    app = QApplication([])
    window = MainWindow(load_settings(None))
    window.show()
    app.processEvents()

    # 队列页：加入（去重）→ 表格镜像 → 选中移除
    window.batch_input.setPlainText("BV1aa411c7mD\nBV1bb411c7mD\nBV1aa411c7mD\n# 注释行")
    window._batch_add()
    assert len(window.batch_items) == 2, "队列去重失败"
    assert window.batch_table.rowCount() == 2
    window.batch_table.selectRow(0)
    window._batch_remove_selected()
    assert len(window.batch_items) == 1 and window.batch_table.rowCount() == 1

    # LLM 接入向导：构建 + patch 结构
    wizard = LlmWizardDialog(window.settings, window)
    wizard.show()
    app.processEvents()
    patch = wizard.patch()
    assert patch["ui"]["llm_wizard_done"] is True and "api_base" in patch["llm"]
    wizard.close()

    # 设置面板：构建（含测试连接按钮）
    settings_dialog = SettingsDialog(window.settings, window)
    settings_dialog.show()
    app.processEvents()
    assert settings_dialog.test_btn.isEnabled()
    settings_dialog.close()

    print(
        f"GUI smoke OK：窗口「{window.windowTitle()}」"
        f"，导航 {window.nav.count()} 项"
        f"，步骤 {window.steps_table.rowCount()} 行"
        f"，历史 {window.history.count()} 条"
        f"，队列 {len(window.batch_items)} 条"
    )
    window.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

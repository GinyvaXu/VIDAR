"""离屏渲染 GUI 页面截图（设计审核 / 版本对比 / 迭代验收用）。

用法：
    .venv\\Scripts\\python.exe scripts/capture_ui.py --out work/design/audit
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

# 视觉截图必须用 Windows 平台插件：offscreen 插件缺少中文字体，会渲染成方块
os.environ.setdefault("QT_QPA_PLATFORM", "windows")

from PySide6.QtWidgets import QApplication  # noqa: E402

from vidar.config import load_settings  # noqa: E402
from vidar.gui.app import MainWindow  # noqa: E402

PAGES = {
    "home": 0,
    "task": 1,
    "queue": 2,
    "library": 3,
    "models": 4,
    "settings": 5,
}

TITLE = "为什么我们总在重造轮子？软件工程的十年循环"

RUNNING_STATE = {
    "resolve": "完成",
    "download": "完成",
    "audio": "完成",
    "asr": "运行中 62%",
}

FAILED_STATE = {
    "resolve": "完成",
    "download": "完成",
    "audio": "完成",
    "asr": "失败",
}

ERROR_MESSAGE = (
    "步骤「语音识别（faster-whisper）」发生未预期错误：Invalid input features shape: "
    "expected an input with shape (1, 128, 3000), but got an input with shape (1, 80, 3000) instead"
)
ERROR_HINT = (
    "模型目录缺少 preprocessor_config.json，特征提取器回落到 80 维。"
    "点「修复模型配置」可自动补齐；也可在模型页执行修复后重跑。"
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="work/design/audit")
    parser.add_argument("--width", type=int, default=1182)
    parser.add_argument("--height", type=int, default=792)
    args = parser.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    app = QApplication([])
    settings = load_settings(None)
    settings.ui.llm_wizard_done = True  # 截图时不要自动弹 LLM 向导
    window = MainWindow(settings)
    window.resize(args.width, args.height)
    window.move(-3000, -3000)  # 屏幕外显示，避免截图时窗口闪烁
    window.show()

    def snap(target: Path) -> None:
        window.update()
        window.repaint()
        app.processEvents()
        window.grab().save(str(target))
        print(f"saved {target}")

    def reset_task() -> None:
        window._render_steps({})
        window.error_card.setVisible(False)
        window.progress.setValue(0)
        window.task_pill.set_status("就绪", "pending")
        window.task_title.setText("还没有运行中的任务")
        window.task_meta.setText("在「工作台」粘贴链接开始；结果会自动保存到资料库")
        window.task_kicker.setText("等待开始")

    def running_task() -> None:
        window._render_steps(RUNNING_STATE)
        window.error_card.setVisible(False)
        window.progress.setValue(620)
        window.task_pill.set_status("识别中 62%", "running")
        window.task_title.setText(TITLE)
        window.task_meta.setText("硬地骇客　·　42:18　·　BV1GJ411x7h7")
        window.task_kicker.setText("任务 04 · 今天 12:37 开始")
        window.log_view.clear()
        for line in (
            "12:37:36  模型路径回退：./models/faster-whisper-large-v3"
            " → D:/VIDAR/models/faster-whisper-large-v3",
            "12:37:36  加载 ASR 模型 large-v3（cuda / float16）",
            "12:37:58  音频时长 42:18，检测语言 zh（置信度 0.99）",
            "12:38:02  已识别 18:20 / 42:18 · 62%，速度 6.4× 实时",
            "12:38:05  ✓ 检查点已保存（第 240 段）",
            "12:38:09  提示：可随时暂停并释放显存，稍后从断点继续",
        ):
            window.log_view.appendPlainText(line)
        window.status_label.setText("已识别 18:20 / 42:18 · 62%")

    for theme in ("light", "dark"):
        window.theme_name = theme
        window._apply_theme()
        app.processEvents()
        reset_task()
        for name, index in PAGES.items():
            window.nav.setCurrentRow(index)
            snap(out / f"{name}-{theme}.png")
            if name == "library" and window.history.count():
                window.history.setCurrentRow(0)
                app.processEvents()
                snap(out / f"library-selected-{theme}.png")

        window.nav.setCurrentRow(PAGES["task"])
        running_task()
        snap(out / f"task-running-{theme}.png")

        failed_task = dict(FAILED_STATE)
        window._render_steps(failed_task)
        window.task_title.setText(TITLE)
        window.task_meta.setText("硬地骇客　·　42:18　·　BV1GJ411x7h7")
        window._show_error("任务失败", ERROR_MESSAGE, ERROR_HINT)
        window.progress.setValue(0)
        snap(out / f"task-failed-{theme}.png")
        reset_task()

    window.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

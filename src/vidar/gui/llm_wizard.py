"""LLM 接入向导：选服务商预设 → 填 Key → 连通性测试 → 保存（首启引导）。

设计：
- 复用服务层 `llm/providers.py`（PRESETS / detect_preset / test_connection）；
- 连通性测试在 QThread 里跑（`ConnectionTestWorker`，设置面板也复用它）；
- 保存时把 `[llm]` 与 `ui.llm_wizard_done = true` 交给主窗口合并写回。
"""

from __future__ import annotations

import os

from PySide6.QtCore import QSize, QThread, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..config import LlmSettings, Settings
from . import theme

_CUSTOM_KEY = "custom"


class ConnectionTestWorker(QThread):
    """最小 chat 请求测连通（1 token）；done(ok, message, hint)。"""

    done = Signal(bool, str, str)

    def __init__(self, llm: LlmSettings) -> None:
        super().__init__()
        self._llm = llm

    def run(self) -> None:
        from ..llm.providers import test_connection

        report = test_connection(self._llm)
        self.done.emit(report.ok, report.message, report.hint)


class LlmWizardDialog(QDialog):
    """三步并一步：选预设 / 填信息 / 测试后保存。"""

    def __init__(self, settings: Settings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._settings = settings
        self._colors = theme.palette(theme.resolve_theme(settings.ui.theme))
        self._test_worker: ConnectionTestWorker | None = None

        self.setWindowTitle("LLM 接入向导")
        self.resize(600, 480)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(12)

        # ---- 头部 ----
        head = QHBoxLayout()
        icon_label = QLabel()
        icon_label.setFixedSize(40, 40)
        icon_label.setPixmap(theme.icon("sparkles", self._colors["accent"], size=40).pixmap(40, 40))
        head.addWidget(icon_label)
        text_box = QVBoxLayout()
        text_box.setSpacing(2)
        title = QLabel("接入大模型（LLM）")
        title.setObjectName("PageTitle")
        subtitle = QLabel(
            "精修文稿、章节切分、摘要、翻译都靠 LLM。填一次即可，之后可在「设置」里修改。"
        )
        subtitle.setObjectName("Muted")
        subtitle.setWordWrap(True)
        text_box.addWidget(title)
        text_box.addWidget(subtitle)
        head.addLayout(text_box, 1)
        layout.addLayout(head)

        # ---- 表单 ----
        self.provider = QComboBox()
        from ..llm.providers import PRESETS

        self._presets = {preset.key: preset for preset in PRESETS}
        for preset in PRESETS:
            self.provider.addItem(preset.label, preset.key)
        self.provider.addItem("自定义（任意 OpenAI 兼容服务）", _CUSTOM_KEY)

        self.api_base = QLineEdit()
        self.llm_model = QLineEdit()
        self.api_key = QLineEdit()
        self.api_key.setEchoMode(QLineEdit.Password)

        form = QFormLayout()
        form.setSpacing(8)
        form.addRow("服务商", self.provider)
        form.addRow("API 地址", self.api_base)
        form.addRow("模型", self.llm_model)
        form.addRow("API Key", self.api_key)
        layout.addLayout(form)

        self.note = QLabel("")
        self.note.setObjectName("Muted")
        self.note.setWordWrap(True)
        layout.addWidget(self.note)

        # ---- 获取 Key / 测试 ----
        row = QHBoxLayout()
        self.key_link_btn = QPushButton("获取 Key")
        self.key_link_btn.setIconSize(QSize(14, 14))
        self.key_link_btn.setVisible(False)
        self.test_btn = QPushButton("测试连接")
        self.test_btn.setIconSize(QSize(14, 14))
        self.test_status = QLabel("")
        self.test_status.setObjectName("Muted")
        self.test_status.setWordWrap(True)
        row.addWidget(self.key_link_btn)
        row.addWidget(self.test_btn)
        row.addWidget(self.test_status, 1)
        layout.addLayout(row)

        layout.addStretch(1)

        # ---- 底部 ----
        bottom = QHBoxLayout()
        self.later_btn = QPushButton("稍后再说")
        self.later_btn.setObjectName("Ghost")
        self.save_btn = QPushButton("保存并完成")
        self.save_btn.setObjectName("Primary")
        bottom.addStretch(1)
        bottom.addWidget(self.later_btn)
        bottom.addWidget(self.save_btn)
        layout.addLayout(bottom)

        self.provider.currentIndexChanged.connect(lambda _index: self._apply_provider())
        self.key_link_btn.clicked.connect(self._open_key_url)
        self.test_btn.clicked.connect(self._test)
        self.save_btn.clicked.connect(self._save)
        self.later_btn.clicked.connect(self.reject)

        self._preset: object | None = None
        self._init_fields()

    # ------------------------------------------------------------------ #
    def _init_fields(self) -> None:
        """按当前配置预选服务商并回填字段。"""
        from ..llm.providers import detect_preset

        llm = self._settings.llm
        current = detect_preset(llm.api_base)
        if current is not None:
            index = self.provider.findData(current.key)
            self.provider.setCurrentIndex(max(0, index))
        else:
            index = self.provider.findData(_CUSTOM_KEY)
            self.provider.setCurrentIndex(max(0, index))
            if llm.api_base:
                self.api_base.setText(llm.api_base)
                self.llm_model.setText(llm.model)
        if llm.api_key:
            self.api_key.setText(llm.api_key)
        self._apply_provider()

    def _apply_provider(self) -> None:
        """预设变更：回填地址/模型/说明/Key 链接（自定义时保持手填）。"""
        key = str(self.provider.currentData())
        preset = self._presets.get(key)
        self._preset = preset
        if preset is None:  # 自定义
            self.note.setText(
                "填写任意 OpenAI 兼容服务的地址（通常以 /v1 结尾）与模型名；"
                "本地 Ollama 地址形如 http://127.0.0.1:11434/v1"
            )
            self.key_link_btn.setVisible(False)
            return
        self.api_base.setText(preset.api_base)
        self.llm_model.setText(preset.model)
        note = preset.note
        env_value = os.environ.get(preset.env_key, "") if preset.env_key else ""
        if env_value:
            note += f"\n已检测到环境变量 {preset.env_key}：Key 可留空（更安全，不写入配置文件）。"
        self.note.setText(note)
        self.key_link_btn.setVisible(bool(preset.key_url))

    def _open_key_url(self) -> None:
        import webbrowser

        preset = self._preset
        if preset is not None and preset.key_url:
            webbrowser.open(preset.key_url)

    # ------------------------------------------------------------------ #
    def _build_llm_settings(self) -> LlmSettings:
        """用当前表单值构造测试用配置（Key 留空时回退该预设的环境变量）。"""
        key = self.api_key.text().strip()
        preset = self._preset
        if not key and preset is not None and preset.env_key:
            key = os.environ.get(preset.env_key, "")
        return LlmSettings(
            api_base=self.api_base.text().strip(),
            api_key=key,
            model=self.llm_model.text().strip() or "deepseek-chat",
        )

    def _test(self) -> None:
        if self._test_worker is not None and self._test_worker.isRunning():
            return
        self.test_btn.setEnabled(False)
        self.test_status.setText("测试中…（1 token 最小请求）")
        self._test_worker = ConnectionTestWorker(self._build_llm_settings())
        self._test_worker.done.connect(self._on_test_done)
        self._test_worker.start()

    def _on_test_done(self, ok: bool, message: str, hint: str) -> None:
        self.test_btn.setEnabled(True)
        color = self._colors["success"] if ok else self._colors["danger"]
        prefix = "✓" if ok else "✗"
        text = f"{prefix} {message}"
        if hint:
            text += f"\n建议：{hint}"
        self.test_status.setText(text)
        self.test_status.setStyleSheet(f"color: {color};")

    def _save(self) -> None:
        api_base = self.api_base.text().strip()
        api_key = self.api_key.text().strip()
        if not api_base:
            QMessageBox.warning(self, "提示", "请选择服务商，或填写自定义 API 地址")
            return
        preset = self._preset
        env_ready = bool(preset is not None and preset.env_key and os.environ.get(preset.env_key))
        is_local = "127.0.0.1" in api_base or "localhost" in api_base
        if not api_key and not env_ready and not is_local:
            answer = QMessageBox.question(
                self,
                "Key 为空",
                "还没有填写 API Key——保存后任务会因未配置 Key 而失败。\n仍要保存吗？",
            )
            if answer != QMessageBox.Yes:
                return
        self.accept()

    def patch(self) -> dict:
        """写回片段：`[llm]` + 向导完成标记。"""
        return {
            "llm": {
                "api_base": self.api_base.text().strip(),
                "api_key": self.api_key.text().strip(),
                "model": self.llm_model.text().strip() or "deepseek-chat",
            },
            "ui": {"llm_wizard_done": True},
        }


__all__ = ["ConnectionTestWorker", "LlmWizardDialog"]

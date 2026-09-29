"""设置面板：编辑常用配置并保存回 config.toml。"""

from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)

from ..config import Settings


class SettingsDialog(QDialog):
    def __init__(self, settings: Settings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("设置")
        self.resize(600, 340)

        self.api_base = QLineEdit(settings.llm.api_base)
        self.api_key = QLineEdit(settings.llm.api_key)
        self.api_key.setEchoMode(QLineEdit.Password)
        self.llm_model = QLineEdit(settings.llm.model)
        self.asr_model = QLineEdit(settings.asr.model)
        self.asr_device = QLineEdit(settings.asr.device)
        self.hf_endpoint = QLineEdit(settings.asr.hf_endpoint)
        self.output_dir = QLineEdit(str(settings.paths.output_dir))
        self.work_dir = QLineEdit(str(settings.paths.work_dir))
        self.keep_media = QCheckBox("保留下载的音频/视频（默认处理完删除）")
        self.keep_media.setChecked(settings.export.keep_media)

        form = QFormLayout()
        form.addRow("LLM API Base", self.api_base)
        form.addRow("LLM API Key", self.api_key)
        form.addRow("LLM 模型", self.llm_model)
        form.addRow("ASR 模型（ID 或本地目录）", self.asr_model)
        form.addRow("ASR 设备（auto / cuda / cpu）", self.asr_device)
        form.addRow("HF 镜像（留空用官方源）", self.hf_endpoint)
        form.addRow("输出目录", self.output_dir)
        form.addRow("工作目录", self.work_dir)
        form.addRow("", self.keep_media)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText("保存")
        buttons.button(QDialogButtonBox.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)

    def patch(self) -> dict[str, Any]:
        """返回本次编辑的配置片段（用于合并写回）。"""
        return {
            "llm": {
                "api_base": self.api_base.text().strip(),
                "api_key": self.api_key.text().strip(),
                "model": self.llm_model.text().strip() or "deepseek-chat",
            },
            "asr": {
                "model": self.asr_model.text().strip() or "large-v3",
                "device": self.asr_device.text().strip() or "auto",
                "hf_endpoint": self.hf_endpoint.text().strip(),
            },
            "paths": {
                "output_dir": self.output_dir.text().strip() or "output",
                "work_dir": self.work_dir.text().strip() or "work",
            },
            "export": {
                "keep_media": self.keep_media.isChecked(),
            },
        }

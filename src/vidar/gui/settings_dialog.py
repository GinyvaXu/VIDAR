"""设置面板：编辑常用配置并保存回 config.toml。"""

from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
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
        self.screenshots = QCheckBox("关键帧截图（供 Word/PDF 配图）")
        self.screenshots.setChecked(settings.export.screenshots)
        self.pdf = QCheckBox("导出 PDF（阅读/打印）")
        self.pdf.setChecked(settings.export.pdf)
        self.pdf_style = QComboBox()
        self.pdf_style.addItems(["newspaper", "paper"])
        self.pdf_style.setCurrentText(settings.export.pdf_style)
        self.pdf_mode = QComboBox()
        self.pdf_mode.addItems(["full", "brief"])
        self.pdf_mode.setCurrentText(settings.export.pdf_mode)
        self.pdf_scale = QComboBox()
        for label, value in (
            ("紧凑（小字，一页更多内容）", "compact"),
            ("标准", "standard"),
            ("大", "large"),
        ):
            self.pdf_scale.addItem(label, value)
        self._select_data(self.pdf_scale, settings.export.pdf_scale)
        self.point_figures = QComboBox()
        for label, value in (("全部要点", "all"), ("隔一取一", "sparse"), ("关闭", "off")):
            self.point_figures.addItem(label, value)
        self._select_data(self.point_figures, settings.export.point_figures)
        self.docx = QCheckBox("导出 Word（.docx，可再编辑）")
        self.docx.setChecked(settings.export.docx)
        self.epub = QCheckBox("导出 EPUB 电子书（微信读书 / 墨水屏）")
        self.epub.setChecked(settings.export.epub)
        self.translate_enabled = QCheckBox("自动翻译外语视频（双语对照）")
        self.translate_enabled.setChecked(settings.translate.enabled)
        self.target_language = QLineEdit(settings.translate.target_language)
        self.bilingual = QCheckBox("双语排版（译文正文 + 原文小字）")
        self.bilingual.setChecked(settings.export.bilingual)

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
        form.addRow("", self.screenshots)
        form.addRow("", self.pdf)
        form.addRow("PDF 风格（newspaper 报刊 / paper 论文）", self.pdf_style)
        form.addRow("PDF 篇幅（full 全文 / brief 精简）", self.pdf_mode)
        form.addRow("PDF 字号档位", self.pdf_scale)
        form.addRow("报刊要点截图密度", self.point_figures)
        form.addRow("", self.docx)
        form.addRow("", self.epub)
        form.addRow("翻译目标语言（zh/en/ja…）", self.target_language)
        form.addRow("", self.translate_enabled)
        form.addRow("", self.bilingual)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText("保存")
        buttons.button(QDialogButtonBox.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)

    @staticmethod
    def _select_data(combo: QComboBox, value: str) -> None:
        index = combo.findData(value)
        combo.setCurrentIndex(index if index >= 0 else 0)

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
            "translate": {
                "enabled": self.translate_enabled.isChecked(),
                "target_language": self.target_language.text().strip() or "zh",
            },
            "export": {
                "keep_media": self.keep_media.isChecked(),
                "screenshots": self.screenshots.isChecked(),
                "pdf": self.pdf.isChecked(),
                "pdf_style": self.pdf_style.currentText(),
                "pdf_mode": self.pdf_mode.currentText(),
                "pdf_scale": self.pdf_scale.currentData(),
                "point_figures": self.point_figures.currentData(),
                "docx": self.docx.isChecked(),
                "epub": self.epub.isChecked(),
                "bilingual": self.bilingual.isChecked(),
            },
        }

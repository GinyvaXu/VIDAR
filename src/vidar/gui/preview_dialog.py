"""排版预览窗口：内嵌 PDF 预览 + 参数面板 + 一键重渲染（不重跑流水线）。

- 参数：风格（报刊/论文）、篇幅（全文/精简）、字号档位、要点截图密度
- 渲染在后台线程执行；预览用 QtPdf（PySide6-Addons 自带，缺失时自动降级为提示）
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..config import Settings
from ..errors import VidarError


def _open_path(path: Path) -> None:
    """用系统默认程序打开文件。"""
    if sys.platform == "win32":
        os.startfile(str(path))  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


class RenderWorker(QThread):
    """后台重渲染（长文档渲染秒级，避免界面卡死）。"""

    done = Signal(dict)
    failed = Signal(str, str)

    def __init__(self, settings: Settings, source: str, options: dict) -> None:
        super().__init__()
        self._settings = settings
        self._source = source
        self._options = options

    def run(self) -> None:
        try:
            from ..rerender import rerender_task

            paths = rerender_task(self._settings, self._source, **self._options)
            self.done.emit({key: str(value) for key, value in paths.items()})
        except VidarError as exc:
            self.failed.emit(exc.message, exc.hint)
        except Exception as exc:  # noqa: BLE001 - 兜底，保证窗口不挂
            self.failed.emit(f"未预期错误：{exc}", "")


class PreviewDialog(QDialog):
    def __init__(self, settings: Settings, source: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"排版预览 — {source}")
        self.resize(1120, 780)
        self._settings = settings
        self._source = source
        self._worker: RenderWorker | None = None
        self._last_pdf: Path | None = None
        self._last_epub: Path | None = None

        # ---- 顶部参数行 ----
        top = QHBoxLayout()
        self.style_combo = QComboBox()
        for label, value in (("报刊风", "newspaper"), ("论文风", "paper")):
            self.style_combo.addItem(label, value)
        self._select(self.style_combo, settings.export.pdf_style)
        self.mode_combo = QComboBox()
        for label, value in (("全文", "full"), ("精简", "brief")):
            self.mode_combo.addItem(label, value)
        self._select(self.mode_combo, settings.export.pdf_mode)
        self.scale_combo = QComboBox()
        for label, value in (
            ("字号：紧凑", "compact"),
            ("字号：标准", "standard"),
            ("字号：大", "large"),
        ):
            self.scale_combo.addItem(label, value)
        self._select(self.scale_combo, settings.export.pdf_scale)
        self.points_combo = QComboBox()
        for label, value in (
            ("要点截图：全部", "all"),
            ("要点截图：隔一取一", "sparse"),
            ("要点截图：关闭", "off"),
        ):
            self.points_combo.addItem(label, value)
        self._select(self.points_combo, settings.export.point_figures)
        self.epub_check = QCheckBox("同时导出电子书（EPUB）")
        self.epub_check.setChecked(settings.export.epub)
        self.bilingual_check = QCheckBox("双语对照")
        self.bilingual_check.setChecked(settings.export.bilingual)
        self.render_btn = QPushButton("重新渲染")
        self.open_btn = QPushButton("用系统程序打开")
        self.open_btn.setEnabled(False)
        self.epub_btn = QPushButton("打开电子书")
        self.epub_btn.setEnabled(False)
        top.addWidget(self.style_combo)
        top.addWidget(self.mode_combo)
        top.addWidget(self.scale_combo)
        top.addWidget(self.points_combo)
        top.addWidget(self.bilingual_check)
        top.addWidget(self.epub_check)
        top.addStretch(1)
        top.addWidget(self.render_btn)
        top.addWidget(self.open_btn)
        top.addWidget(self.epub_btn)

        # ---- 预览区（QtPdf；缺失时降级为提示） ----
        self.view = None
        self._pdf_doc = None
        self._pdf_error_ok = None
        try:
            from PySide6.QtPdf import QPdfDocument
            from PySide6.QtPdfWidgets import QPdfView

            self._pdf_doc = QPdfDocument(self)
            self._pdf_error_ok = QPdfDocument.Error.None_
            self.view = QPdfView(self)
            self.view.setDocument(self._pdf_doc)
            self.view.setPageMode(QPdfView.PageMode.MultiPage)
            self.view.setZoomMode(QPdfView.ZoomMode.FitToWidth)
            view_widget: QWidget = self.view
        except Exception as exc:  # noqa: BLE001 - 无 QtPdf 时降级
            label = QLabel(
                f"当前环境没有 PDF 预览组件（{exc}）。\n"
                "仍可「重新渲染」后用系统程序打开查看。"
            )
            label.setWordWrap(True)
            view_widget = label

        self.status = QLabel("准备渲染…")
        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(view_widget, 1)
        layout.addWidget(self.status)

        self.render_btn.clicked.connect(self.render)
        self.open_btn.clicked.connect(self._open_pdf)
        self.epub_btn.clicked.connect(self._open_epub)
        self.render()

    @staticmethod
    def _select(combo: QComboBox, value: str) -> None:
        index = combo.findData(value)
        combo.setCurrentIndex(index if index >= 0 else 0)

    # ------------------------------------------------------------------ #
    def render(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            return
        self.render_btn.setEnabled(False)
        self.status.setText("渲染中…（长文档可能需要几秒）")
        options = {
            "style": self.style_combo.currentData(),
            "mode": self.mode_combo.currentData(),
            "scale": self.scale_combo.currentData(),
            "point_figures": self.points_combo.currentData(),
            "bilingual": self.bilingual_check.isChecked(),
            "epub": self.epub_check.isChecked(),
        }
        self._worker = RenderWorker(self._settings, self._source, options)
        self._worker.done.connect(self._on_done)
        self._worker.failed.connect(self._on_failed)
        self._worker.finished.connect(lambda: self.render_btn.setEnabled(True))
        self._worker.start()

    def _on_done(self, paths: dict) -> None:
        def existing(key: str) -> Path | None:
            value = paths.get(key)
            if not value:
                return None
            path = Path(str(value))
            return path if path.is_file() else None

        self._last_pdf = existing("pdf")
        self.open_btn.setEnabled(self._last_pdf is not None)
        self._last_epub = existing("epub")
        self.epub_btn.setEnabled(self._last_epub is not None)
        if self._last_pdf is not None and self._pdf_doc is not None:
            error = self._pdf_doc.load(str(self._last_pdf))
            if self._pdf_error_ok is not None and error != self._pdf_error_ok:
                self.status.setText(f"预览加载失败（错误码 {error}）：{self._last_pdf}")
                return
        rendered: list[str] = []
        if self._last_pdf is not None:
            rendered.append(f"PDF：{self._last_pdf.name}")
        if self._last_epub is not None:
            rendered.append(f"EPUB：{self._last_epub.name}")
        self.status.setText("✓ 已渲染 " + "；".join(rendered) if rendered else "未产出文件")

    def _on_failed(self, message: str, hint: str) -> None:
        self.status.setText("渲染失败")
        QMessageBox.critical(self, "渲染失败", message + (f"\n\n{hint}" if hint else ""))

    def _open_pdf(self) -> None:
        if self._last_pdf is not None:
            _open_path(self._last_pdf)

    def _open_epub(self) -> None:
        if self._last_epub is not None:
            _open_path(self._last_epub)


__all__ = ["PreviewDialog"]

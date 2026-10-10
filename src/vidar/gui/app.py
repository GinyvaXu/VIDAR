"""VIDAR 桌面 GUI：侧边栏导航 + 卡片式任务流（自绘现代风）。

- 页面：任务（跑流水线）/ 历史（任务与产物）/ 模型（下载与管理）/ 关于
- 主题：明/暗双主题（`[ui].theme = auto|light|dark`），设计 token 集中在 gui/theme.py
- 耗时操作全部在 QThread：流水线 / 排版渲染（预览窗口）/ 模型下载，经信号回 UI
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import subprocess
import sys
import webbrowser
from pathlib import Path

from PySide6.QtCore import QObject, QSize, Qt, QThread, Signal
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .. import __version__
from ..config import (
    Settings,
    deep_merge,
    default_config_path,
    export_settings_dict,
    load_settings,
    write_config_dict,
)
from ..errors import CancelledError, VidarError
from ..logging_setup import setup_logging
from ..pipeline import (
    STEP_ORDER,
    PipelineRunner,
    RunContext,
    default_steps,
    make_key,
    resolve_path,
)
from ..utils.text import slugify
from . import theme
from .preview_dialog import PreviewDialog
from .settings_dialog import SettingsDialog

_STEP_TITLES = {step.name: step.title for step in default_steps()}  # type: ignore[attr-defined]
_NAV_ITEMS = (
    ("task", "任务", "play"),
    ("history", "历史", "clock"),
    ("models", "模型", "cpu"),
    ("about", "关于", "info"),
)


def _open_path(path: Path) -> None:
    if os.name == "nt":
        os.startfile(str(path))  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


def _card() -> tuple[QFrame, QVBoxLayout]:
    frame = QFrame()
    frame.setObjectName("Card")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(18, 16, 18, 16)
    layout.setSpacing(10)
    return frame, layout


class _Emitter(QObject):
    line = Signal(str)


class _QtLogHandler(logging.Handler):
    """把 vidar 日志转发到 GUI 日志面板。"""

    def __init__(self, emitter: _Emitter) -> None:
        super().__init__(logging.INFO)
        self._emitter = emitter
        self.setFormatter(logging.Formatter("%(asctime)s  %(message)s", "%H:%M:%S"))

    def emit(self, record: logging.LogRecord) -> None:
        with contextlib.suppress(Exception):  # 日志不能反过来影响主流程
            self._emitter.line.emit(self.format(record))


class PipelineWorker(QThread):
    event = Signal(str, dict)
    failed = Signal(str, str)
    ok = Signal()

    def __init__(self, settings: Settings, source: str, from_step: str | None) -> None:
        super().__init__()
        self._settings = settings
        self._source = source
        self._from_step = from_step
        self.ctx: RunContext | None = None

    def run(self) -> None:
        try:
            ctx = RunContext(self._settings, self._source)
            self.ctx = ctx
            ctx.on_event = lambda event, data: self.event.emit(event, data)
            status = PipelineRunner(ctx, default_steps()).run(from_step=self._from_step)  # type: ignore[arg-type]
            if status == "paused":
                self.event.emit("run_paused", {})
            else:
                self.ok.emit()
        except CancelledError:
            self.event.emit("cancelled", {})
        except VidarError as exc:
            self.failed.emit(exc.message, exc.hint)
        except Exception as exc:  # noqa: BLE001 - 兜底，保证 UI 不挂
            self.failed.emit(f"未预期错误：{exc}", "")

    def cancel(self) -> None:
        if self.ctx is not None:
            self.ctx.cancel()

    def pause(self) -> None:
        if self.ctx is not None:
            self.ctx.pause()

    def resume(self) -> None:
        if self.ctx is not None:
            self.ctx.resume()

    def release_resources(self) -> None:
        if self.ctx is not None:
            self.ctx.release_resources()


class ModelDownloadWorker(QThread):
    """模型下载（分片并行 / 断点续传），进度信号回 UI。"""

    progress = Signal(str, int, int)
    done = Signal(int, int)
    failed = Signal(str, str)

    def __init__(self, model: str, target: Path, source: str = "hf-mirror") -> None:
        super().__init__()
        self._model = model
        self._target = target
        self._source = source
        self._cancelled = False

    def run(self) -> None:
        from ..model_store import download_model

        try:
            downloaded, skipped = download_model(
                self._model,
                self._target,
                source=self._source,
                on_progress=lambda name, done, total: self.progress.emit(name, done, total),
                should_cancel=lambda: self._cancelled,
            )
            self.done.emit(len(downloaded), len(skipped))
        except VidarError as exc:
            self.failed.emit(exc.message, exc.hint)
        except Exception as exc:  # noqa: BLE001 - 兜底
            self.failed.emit(f"未预期错误：{exc}", "")

    def cancel(self) -> None:
        self._cancelled = True


class MainWindow(QMainWindow):
    def __init__(self, settings: Settings) -> None:
        super().__init__()
        self.settings = settings
        self.theme_name = theme.resolve_theme(settings.ui.theme)
        self.worker: PipelineWorker | None = None
        self.model_worker: ModelDownloadWorker | None = None
        self.last_key: str | None = None
        self._model_progress_state: tuple[float, int] | None = None

        self.setWindowTitle("VIDAR — B站视频知识提取")
        self.resize(1180, 760)
        self.setMinimumSize(980, 640)

        self._emitter = _Emitter()
        self._emitter.line.connect(self._append_log)
        logging.getLogger("vidar").addHandler(_QtLogHandler(self._emitter))

        self._build_ui()
        self._apply_theme()
        self._render_steps({})
        self.refresh_history()
        self.refresh_models()
        self._append_log("就绪：粘贴链接 → 开始。首次跑某条视频会自动解析并下载音频。")

    # ------------------------------------------------------------------ #
    # 界面搭建
    # ------------------------------------------------------------------ #
    def _build_ui(self) -> None:
        root = QWidget()
        root.setObjectName("Root")
        self.setCentralWidget(root)
        layout = QHBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.pages = QStackedWidget()
        layout.addWidget(self._build_sidebar())
        self.pages.addWidget(self._build_task_page())
        self.pages.addWidget(self._build_history_page())
        self.pages.addWidget(self._build_models_page())
        self.pages.addWidget(self._build_about_page())
        layout.addWidget(self.pages, 1)

    def _build_sidebar(self) -> QWidget:
        side = QFrame()
        side.setObjectName("Sidebar")
        side.setFixedWidth(216)
        layout = QVBoxLayout(side)
        layout.setContentsMargins(14, 18, 14, 14)
        layout.setSpacing(12)

        logo_row = QHBoxLayout()
        self.logo_label = QLabel()
        self.logo_label.setFixedSize(38, 38)
        logo_row.addWidget(self.logo_label)
        logo_text = QVBoxLayout()
        logo_text.setSpacing(0)
        title = QLabel("VIDAR")
        title.setObjectName("LogoTitle")
        subtitle = QLabel("B站视频 → 知识文档")
        subtitle.setObjectName("LogoSub")
        logo_text.addWidget(title)
        logo_text.addWidget(subtitle)
        logo_row.addLayout(logo_text, 1)
        layout.addLayout(logo_row)

        self.nav = QListWidget()
        self.nav.setObjectName("NavList")
        self.nav.setIconSize(QSize(18, 18))
        self.nav.setFocusPolicy(Qt.NoFocus)
        for _key, text, _icon_name in _NAV_ITEMS:
            self.nav.addItem(QListWidgetItem(text))
        self.nav.setCurrentRow(0)
        self.nav.currentRowChanged.connect(self.pages.setCurrentIndex)
        layout.addWidget(self.nav, 1)

        self.settings_btn = QPushButton("设置")
        self.settings_btn.setObjectName("Ghost")
        self.settings_btn.setIconSize(QSize(16, 16))
        self.settings_btn.clicked.connect(self._open_settings)
        layout.addWidget(self.settings_btn)

        self.theme_btn = QPushButton("深色模式")
        self.theme_btn.setObjectName("Ghost")
        self.theme_btn.setIconSize(QSize(16, 16))
        self.theme_btn.clicked.connect(self._toggle_theme)
        layout.addWidget(self.theme_btn)

        version_label = QLabel(f"v{__version__} · 本地识别 + 大模型整理")
        version_label.setObjectName("VersionLabel")
        version_label.setWordWrap(True)
        layout.addWidget(version_label)
        return side

    def _page_header(self, title: str, subtitle: str) -> QVBoxLayout:
        box = QVBoxLayout()
        box.setSpacing(2)
        head = QLabel(title)
        head.setObjectName("PageTitle")
        sub = QLabel(subtitle)
        sub.setObjectName("PageSub")
        sub.setWordWrap(True)
        box.addWidget(head)
        box.addWidget(sub)
        return box

    # ---- 任务页 ----
    def _build_task_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(14)
        layout.addLayout(
            self._page_header("任务", "粘贴一条 B 站链接，自动产出 Markdown / PDF / EPUB 全套文档")
        )

        input_card, input_layout = _card()
        row = QHBoxLayout()
        self.url_edit = QLineEdit()
        self.url_edit.setPlaceholderText("粘贴 BV 号或视频链接，例如 BV1xx411c7mD")
        self.url_edit.returnPressed.connect(self._start)
        self.from_combo = QComboBox()
        self.from_combo.addItem("从头开始", None)
        for name in STEP_ORDER:
            self.from_combo.addItem(f"从 {name} 重跑", name)
        self.start_btn = QPushButton("开始")
        self.start_btn.setObjectName("Primary")
        self.start_btn.setIconSize(QSize(16, 16))
        self.cancel_btn = QPushButton("取消")
        self.cancel_btn.setObjectName("Danger")
        self.cancel_btn.setIconSize(QSize(16, 16))
        self.cancel_btn.setEnabled(False)
        row.addWidget(self.url_edit, 1)
        row.addWidget(self.from_combo)
        row.addWidget(self.start_btn)
        row.addWidget(self.cancel_btn)
        input_layout.addLayout(row)

        actions = QHBoxLayout()
        self.open_out_btn = QPushButton("打开输出目录")
        self.open_notes_btn = QPushButton("打开文稿")
        self.preview_btn = QPushButton("预览排版…")
        for button in (self.open_out_btn, self.open_notes_btn, self.preview_btn):
            button.setIconSize(QSize(16, 16))
        actions.addWidget(self.open_out_btn)
        actions.addWidget(self.open_notes_btn)
        actions.addWidget(self.preview_btn)
        actions.addStretch(1)
        input_layout.addLayout(actions)
        layout.addWidget(input_card)

        progress_card, progress_layout = _card()
        progress_row = QHBoxLayout()
        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setValue(0)
        self.pause_btn = QPushButton("暂停")
        self.pause_btn.setIconSize(QSize(16, 16))
        self.pause_btn.setVisible(False)
        self.resume_btn = QPushButton("继续")
        self.resume_btn.setIconSize(QSize(16, 16))
        self.resume_btn.setVisible(False)
        self.release_btn = QPushButton("释放显存")
        self.release_btn.setIconSize(QSize(16, 16))
        self.release_btn.setVisible(False)
        self.status_label = QLabel("就绪")
        self.status_label.setObjectName("Muted")
        progress_row.addWidget(self.progress, 1)
        progress_row.addWidget(self.pause_btn)
        progress_row.addWidget(self.resume_btn)
        progress_row.addWidget(self.release_btn)
        progress_row.addWidget(self.status_label)
        progress_layout.addLayout(progress_row)

        self.steps_table = QTableWidget(0, 3)
        self.steps_table.setHorizontalHeaderLabels(["步骤", "内容", "状态"])
        self.steps_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.steps_table.verticalHeader().setVisible(False)
        self.steps_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.steps_table.setSelectionMode(QTableWidget.NoSelection)
        self.steps_table.setShowGrid(False)
        self.steps_table.setFixedHeight(212)
        progress_layout.addWidget(self.steps_table)
        layout.addWidget(progress_card)

        log_card, log_layout = _card()
        log_title = QLabel("运行日志")
        log_title.setObjectName("CardTitle")
        log_layout.addWidget(log_title)
        self.log_view = QPlainTextEdit()
        self.log_view.setObjectName("LogView")
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(5000)
        log_layout.addWidget(self.log_view, 1)
        layout.addWidget(log_card, 1)

        self.start_btn.clicked.connect(self._start)
        self.cancel_btn.clicked.connect(self._cancel)
        self.pause_btn.clicked.connect(self._pause)
        self.resume_btn.clicked.connect(self._resume)
        self.release_btn.clicked.connect(self._release_resources)
        self.open_out_btn.clicked.connect(lambda: self._open_result(notes=False))
        self.open_notes_btn.clicked.connect(lambda: self._open_result(notes=True))
        self.preview_btn.clicked.connect(self._open_preview)
        return page

    # ---- 历史页 ----
    def _build_history_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(14)
        layout.addLayout(
            self._page_header(
                "历史",
                "每个任务都记住进度：一键「继续」自动定位未完成步骤，或在步骤明细里点任意步骤「从此步重跑」",
            )
        )

        card, card_layout = _card()
        self.history = QListWidget()
        self.history.itemDoubleClicked.connect(self._load_history_item)
        self.history.currentItemChanged.connect(self._on_history_selected)
        card_layout.addWidget(self.history, 1)

        row = QHBoxLayout()
        self.history_resume_btn = QPushButton("继续（自动定位未完成）")
        self.history_resume_btn.setObjectName("Primary")
        self.history_rerun_btn = QPushButton("从选中步骤重跑")
        self.history_open_out_btn = QPushButton("打开输出目录")
        self.history_open_notes_btn = QPushButton("打开文稿")
        self.history_preview_btn = QPushButton("预览排版…")
        self.history_refresh_btn = QPushButton("刷新")
        for button in (
            self.history_resume_btn,
            self.history_rerun_btn,
            self.history_open_out_btn,
            self.history_open_notes_btn,
            self.history_preview_btn,
            self.history_refresh_btn,
        ):
            button.setIconSize(QSize(16, 16))
            row.addWidget(button)
        row.addStretch(1)
        card_layout.addLayout(row)
        layout.addWidget(card, 1)

        detail_card, detail_layout = _card()
        detail_title = QLabel("步骤明细（选中任务后显示；选中一行即可「从选中步骤重跑」）")
        detail_title.setObjectName("CardTitle")
        detail_layout.addWidget(detail_title)
        self.history_detail = QTableWidget(0, 3)
        self.history_detail.setHorizontalHeaderLabels(["步骤", "内容", "状态"])
        self.history_detail.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.history_detail.verticalHeader().setVisible(False)
        self.history_detail.setEditTriggers(QTableWidget.NoEditTriggers)
        self.history_detail.setSelectionBehavior(QTableWidget.SelectRows)
        self.history_detail.setSelectionMode(QTableWidget.SingleSelection)
        self.history_detail.setShowGrid(False)
        self.history_detail.setFixedHeight(230)
        detail_layout.addWidget(self.history_detail)
        layout.addWidget(detail_card)

        self.history_resume_btn.clicked.connect(self._history_resume)
        self.history_rerun_btn.clicked.connect(self._history_rerun_from_selected)
        self.history_open_out_btn.clicked.connect(lambda: self._open_result(notes=False))
        self.history_open_notes_btn.clicked.connect(lambda: self._open_result(notes=True))
        self.history_preview_btn.clicked.connect(self._open_preview)
        self.history_refresh_btn.clicked.connect(self.refresh_history)
        return page

    # ---- 模型页 ----
    def _build_models_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(14)
        layout.addLayout(
            self._page_header(
                "模型", "语音识别模型：按显存推荐档位，分片并行下载、断点续传、大小校验"
            )
        )

        self.model_banner = QLabel("正在检测显卡…")
        self.model_banner.setObjectName("Banner")
        self.model_banner.setWordWrap(True)
        self.model_banner.setContentsMargins(12, 10, 12, 10)
        layout.addWidget(self.model_banner)

        card, card_layout = _card()
        self.model_table = QTableWidget(0, 5)
        self.model_table.setHorizontalHeaderLabels(["型号", "大小", "显存建议", "状态", "说明"])
        header = self.model_table.horizontalHeader()
        header.setSectionResizeMode(4, QHeaderView.Stretch)
        self.model_table.verticalHeader().setVisible(False)
        self.model_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.model_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.model_table.setSelectionMode(QTableWidget.SingleSelection)
        self.model_table.setShowGrid(False)
        self.model_table.setFixedHeight(190)
        card_layout.addWidget(self.model_table)

        row = QHBoxLayout()
        self.model_download_btn = QPushButton("下载所选")
        self.model_download_btn.setObjectName("Primary")
        self.model_cancel_btn = QPushButton("取消")
        self.model_cancel_btn.setObjectName("Danger")
        self.model_check_btn = QPushButton("校验所选")
        self.model_refresh_btn = QPushButton("刷新")
        for button in (
            self.model_download_btn,
            self.model_cancel_btn,
            self.model_check_btn,
            self.model_refresh_btn,
        ):
            button.setIconSize(QSize(16, 16))
            row.addWidget(button)
        row.addStretch(1)
        card_layout.addLayout(row)

        self.model_progress = QProgressBar()
        self.model_progress.setRange(0, 1000)
        self.model_progress.setValue(0)
        self.model_status = QLabel("未开始")
        self.model_status.setObjectName("Muted")
        status_row = QHBoxLayout()
        status_row.addWidget(self.model_progress, 1)
        status_row.addWidget(self.model_status)
        card_layout.addLayout(status_row)
        layout.addWidget(card)
        layout.addStretch(1)

        self.model_download_btn.clicked.connect(self._download_selected_model)
        self.model_cancel_btn.clicked.connect(self._cancel_model_download)
        self.model_check_btn.clicked.connect(self._check_selected_model)
        self.model_refresh_btn.clicked.connect(self.refresh_models)
        return page

    # ---- 关于页 ----
    def _build_about_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(14)
        layout.addLayout(self._page_header("关于", "把 B 站视频变成可读、可检索、可打印的知识文档"))

        card, card_layout = _card()
        head = QHBoxLayout()
        self.about_icon = QLabel()
        self.about_icon.setFixedSize(72, 72)
        head.addWidget(self.about_icon)
        text_box = QVBoxLayout()
        name = QLabel(f"VIDAR v{__version__}")
        name.setObjectName("PageTitle")
        tagline = QLabel(
            "本地语音识别（faster-whisper）+ 大模型精修/摘要/翻译，"
            "一键产出 Markdown / PDF / Word / EPUB"
        )
        tagline.setWordWrap(True)
        tagline.setObjectName("PageSub")
        text_box.addWidget(name)
        text_box.addWidget(tagline)
        head.addLayout(text_box, 1)
        card_layout.addLayout(head)

        steps_title = QLabel("快速上手")
        steps_title.setObjectName("CardTitle")
        card_layout.addWidget(steps_title)
        for line in (
            "1. 配置 LLM：设置 → 填 API Base / Key（或命令行 vidar llm setup <预设> --write）",
            "2. 下载模型：本页选档位下载（推荐 large-v3；N 卡首次使用可在设置中确认 device=auto）",
            "3. 任务页粘贴链接 → 开始；完成后在历史页打开文稿 / 预览排版 / 导入微信读书",
        ):
            label = QLabel(line)
            label.setWordWrap(True)
            label.setObjectName("Muted")
            card_layout.addWidget(label)
        layout.addWidget(card)

        links_card, links_layout = _card()
        links_title = QLabel("资源")
        links_title.setObjectName("CardTitle")
        links_layout.addWidget(links_title)
        row = QHBoxLayout()
        homepage_btn = QPushButton("项目主页")
        config_btn = QPushButton("打开配置文件")
        logs_btn = QPushButton("打开日志目录")
        output_btn = QPushButton("打开输出目录")
        for button in (homepage_btn, config_btn, logs_btn, output_btn):
            button.setIconSize(QSize(16, 16))
            row.addWidget(button)
        row.addStretch(1)
        links_layout.addLayout(row)
        layout.addWidget(links_card)
        layout.addStretch(1)

        homepage_btn.clicked.connect(
            lambda: webbrowser.open("https://github.com/GinyvaXu/VIDAR")
        )
        config_btn.clicked.connect(self._open_config_file)
        logs_btn.clicked.connect(self._open_logs_dir)
        output_btn.clicked.connect(lambda: self._open_result(notes=False))
        return page

    # ------------------------------------------------------------------ #
    # 主题
    # ------------------------------------------------------------------ #
    def _apply_theme(self) -> None:
        app = QApplication.instance()
        if app is not None:
            app.setStyleSheet(theme.build_qss(self.theme_name))
        colors = theme.palette(self.theme_name)
        for index, (_key, _text, icon_name) in enumerate(_NAV_ITEMS):
            item = self.nav.item(index)
            if item is not None:
                item.setIcon(theme.icon(icon_name, colors["text_muted"], size=18))
        self.theme_btn.setText(
            "深色模式" if self.theme_name == "light" else "浅色模式"
        )
        self.theme_btn.setIcon(
            theme.icon(
                "moon" if self.theme_name == "light" else "sun",
                colors["text_muted"],
                size=16,
            )
        )
        self.settings_btn.setIcon(theme.icon("settings", colors["text_muted"], size=16))
        self.start_btn.setIcon(theme.icon("rocket", "#FFFFFF", size=16))
        self.cancel_btn.setIcon(theme.icon("square", colors["text"], size=16))
        self.pause_btn.setIcon(theme.icon("pause", colors["text"], size=16))
        self.resume_btn.setIcon(theme.icon("play", colors["text"], size=16))
        self.release_btn.setIcon(theme.icon("hard-drive", colors["text_muted"], size=16))
        self.open_out_btn.setIcon(theme.icon("folder-open", colors["text"], size=16))
        self.open_notes_btn.setIcon(theme.icon("file-text", colors["text"], size=16))
        self.preview_btn.setIcon(theme.icon("eye", colors["text"], size=16))
        self.history_resume_btn.setIcon(theme.icon("play", "#FFFFFF", size=16))
        self.history_rerun_btn.setIcon(theme.icon("refresh-cw", colors["text"], size=16))
        self.history_open_out_btn.setIcon(theme.icon("folder-open", colors["text"], size=16))
        self.history_open_notes_btn.setIcon(theme.icon("file-text", colors["text"], size=16))
        self.history_preview_btn.setIcon(theme.icon("eye", colors["text"], size=16))
        self.history_refresh_btn.setIcon(theme.icon("refresh-cw", colors["text"], size=16))
        self.model_download_btn.setIcon(theme.icon("download", "#FFFFFF", size=16))
        self.model_cancel_btn.setIcon(theme.icon("x", colors["text"], size=16))
        self.model_check_btn.setIcon(theme.icon("gauge", colors["text"], size=16))
        self.model_refresh_btn.setIcon(theme.icon("refresh-cw", colors["text"], size=16))
        self.logo_label.setPixmap(theme.app_icon().pixmap(38, 38))
        self.about_icon.setPixmap(theme.app_icon().pixmap(72, 72))

    def _toggle_theme(self) -> None:
        self.theme_name = "dark" if self.theme_name == "light" else "light"
        self._apply_theme()
        try:
            data = deep_merge(
                export_settings_dict(self.settings), {"ui": {"theme": self.theme_name}}
            )
            new_settings = Settings.model_validate(data)
            new_settings.config_path = self.settings.config_path
            target = new_settings.config_path or default_config_path()
            write_config_dict(target, export_settings_dict(new_settings))
            self.settings = new_settings
        except OSError:
            self._append_log("主题偏好保存失败（不影响本次使用）")

    # ------------------------------------------------------------------ #
    # 任务控制
    # ------------------------------------------------------------------ #
    def _start(self) -> None:
        source = self.url_edit.text().strip()
        if not source:
            QMessageBox.warning(self, "提示", "请先粘贴 BV 号或视频链接")
            return
        if self.worker is not None and self.worker.isRunning():
            QMessageBox.information(self, "提示", "已有任务在运行，请先等待或取消")
            return

        self.last_key = make_key(source)
        self._render_steps({name: "等待" for name in STEP_ORDER})
        self.progress.setValue(0)
        self.status_label.setText("启动中…")
        self.log_view.clear()
        self._set_running(True)

        self.worker = PipelineWorker(self.settings, source, self.from_combo.currentData())
        self.worker.event.connect(self._on_event)
        self.worker.failed.connect(self._on_failed)
        self.worker.ok.connect(self._on_ok)
        self.worker.finished.connect(self._on_worker_finished)
        self.worker.start()

    def _cancel(self) -> None:
        if self.worker is not None:
            self._append_log("已请求取消，等待当前步骤安全退出…")
            self.cancel_btn.setEnabled(False)
            self.worker.cancel()

    def _set_running(self, running: bool) -> None:
        self.start_btn.setEnabled(not running)
        self.cancel_btn.setEnabled(running)
        self.url_edit.setEnabled(not running)
        self.from_combo.setEnabled(not running)
        self.pause_btn.setVisible(running)
        self.pause_btn.setEnabled(running)
        self.resume_btn.setVisible(False)
        self.release_btn.setVisible(False)
        self.release_btn.setEnabled(True)

    def _pause(self) -> None:
        if self.worker is None or not self.worker.isRunning():
            return
        self.worker.pause()
        self.pause_btn.setVisible(False)
        self.resume_btn.setVisible(True)
        self.release_btn.setVisible(True)
        self.release_btn.setEnabled(True)
        self.status_label.setText("已暂停")

    def _resume(self) -> None:
        if self.worker is None:
            return
        self.worker.resume()
        self.resume_btn.setVisible(False)
        self.release_btn.setVisible(False)
        self.pause_btn.setVisible(True)
        self.status_label.setText("继续运行…")

    def _release_resources(self) -> None:
        if self.worker is None:
            return
        self.release_btn.setEnabled(False)
        self.status_label.setText("正在释放资源（当前步骤保存检查点后退出）…")
        self.worker.release_resources()

    # ------------------------------------------------------------------ #
    # 事件与日志
    # ------------------------------------------------------------------ #
    def _on_event(self, event: str, data: dict) -> None:
        step = str(data.get("step", ""))
        if event == "progress":
            done = float(data.get("done", 0) or 0)
            total = float(data.get("total", 0) or 0)
            if total > 0:
                self.progress.setValue(int(1000 * min(1.0, done / total)))
            message = str(data.get("message", ""))
            if message:
                self.status_label.setText(message)
        elif event == "step_started":
            self._set_step_status(step, "运行中…")
            self.status_label.setText(str(data.get("title", step)))
        elif event == "step_done":
            self._set_step_status(step, "完成 ✓")
            self.progress.setValue(0)
        elif event == "step_skipped":
            self._set_step_status(step, "跳过（已完成）")
        elif event == "step_cancelled":
            self._set_step_status(step, "已取消")
        elif event == "step_failed":
            self._set_step_status(step, "失败 ✗")
            self._append_log(f"步骤 [{step}] 失败：{data.get('error', '')}")
        elif event == "paused":
            self.status_label.setText("已暂停（可「继续」；或「释放显存」后从历史页续跑）")
        elif event == "resumed":
            self.status_label.setText("继续运行…")
        elif event == "step_paused":
            self._append_log(f"步骤 [{step}] 已保存检查点并暂停（可继续）")
        elif event == "run_paused":
            self.status_label.setText("已暂停并释放资源：历史页可一键「继续」")
            self._append_log("运行已暂停：检查点已保存，继续时不会重跑已完成部分")
        elif event == "run_finished":
            self.status_label.setText("全部完成")
            self._append_log("全部步骤完成。")
        elif event == "cancelled":
            self.status_label.setText("已取消")
            self._append_log("任务已取消（可从中断步骤续跑）。")

    def _on_failed(self, message: str, hint: str) -> None:
        self.status_label.setText("失败")
        text = message + (f"\n\n{hint}" if hint else "")
        self._append_log(f"失败：{text}")
        QMessageBox.critical(self, "任务失败", text)

    def _on_ok(self) -> None:
        self.status_label.setText("完成 ✓")

    def _on_worker_finished(self) -> None:
        self._set_running(False)
        self.refresh_history()

    def _append_log(self, text: str) -> None:
        self.log_view.appendPlainText(text)
        scrollbar = self.log_view.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    # ------------------------------------------------------------------ #
    # 步骤表格
    # ------------------------------------------------------------------ #
    def _render_steps(self, statuses: dict[str, str]) -> None:
        self.steps_table.setRowCount(0)
        for name in STEP_ORDER:
            row = self.steps_table.rowCount()
            self.steps_table.insertRow(row)
            self.steps_table.setItem(row, 0, QTableWidgetItem(name))
            self.steps_table.setItem(row, 1, QTableWidgetItem(_STEP_TITLES.get(name, "")))
            self.steps_table.setItem(row, 2, QTableWidgetItem(statuses.get(name, "等待")))

    def _set_step_status(self, name: str, text: str) -> None:
        for row in range(self.steps_table.rowCount()):
            item = self.steps_table.item(row, 0)
            if item is not None and item.text() == name:
                self.steps_table.setItem(row, 2, QTableWidgetItem(text))
                return

    # ------------------------------------------------------------------ #
    # 历史任务
    # ------------------------------------------------------------------ #
    def refresh_history(self) -> None:
        self.history.clear()
        work_root = resolve_path(self.settings.paths.work_dir)
        if not work_root.exists():
            return
        folders = sorted(
            (p for p in work_root.iterdir() if p.is_dir() and not p.name.startswith("_")),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        for folder in folders:
            state_path = folder / "state.json"
            if not state_path.exists():
                continue
            try:
                state = json.loads(state_path.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                continue
            steps = state.get("steps", {})
            done = sum(1 for rec in steps.values() if rec.get("status") == "done")
            failed = [name for name, rec in steps.items() if rec.get("status") == "failed"]
            title = ""
            url = ""
            meta_path = folder / "meta.json"
            if meta_path.exists():
                try:
                    meta = json.loads(meta_path.read_text(encoding="utf-8"))
                    title = str(meta.get("title") or "")
                    url = str(meta.get("url") or "")
                except (ValueError, OSError):
                    pass
            paused_step = next(
                (
                    name
                    for name, rec in steps.items()
                    if rec.get("status") == "pending" and "已暂停" in str(rec.get("error") or "")
                ),
                None,
            )
            colors = theme.palette(self.theme_name)
            if failed:
                suffix = f"失败于 {failed[0]}"
                icon_name, icon_color = "triangle-alert", colors["danger"]
            elif paused_step:
                suffix = f"已暂停（{paused_step}）"
                icon_name, icon_color = "pause", colors["warning"]
            elif done >= len(STEP_ORDER):
                suffix = "全部完成"
                icon_name, icon_color = "circle-check", colors["success"]
            else:
                suffix = f"{done}/{len(STEP_ORDER)} 步"
                icon_name, icon_color = "play", colors["text_muted"]
            item = QListWidgetItem(f"{folder.name} · {title or '（未解析）'} · {suffix}")
            item.setData(Qt.UserRole, {"key": folder.name, "url": url or folder.name})
            item.setIcon(theme.icon(icon_name, icon_color, size=16))
            self.history.addItem(item)

    def _load_selected_history(self) -> None:
        item = self.history.currentItem()
        if item is None:
            QMessageBox.information(self, "提示", "先在列表里选一条任务")
            return
        self._load_history_item(item)

    # ---- 进度记忆：步骤明细 / 继续 / 从某步重跑 ----
    def _on_history_selected(self, current, _previous=None) -> None:  # noqa: ANN001
        if current is None:
            self.history_detail.setRowCount(0)
            return
        key = str((current.data(Qt.UserRole) or {}).get("key") or "")
        self._load_history_detail(key)

    def _history_state(self, key: str) -> dict:
        state_path = resolve_path(self.settings.paths.work_dir) / key / "state.json"
        if not state_path.exists():
            return {}
        try:
            payload = json.loads(state_path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            return {}
        steps = payload.get("steps")
        return steps if isinstance(steps, dict) else {}

    def _load_history_detail(self, key: str) -> None:
        self.history_detail.setRowCount(0)
        steps = self._history_state(key)
        labels = {"done": "完成 ✓", "failed": "失败 ✗", "running": "运行中…", "pending": "等待"}
        for name in STEP_ORDER:
            record = steps.get(name) or {}
            status = str(record.get("status") or "")
            note = str(record.get("error") or "")
            label = labels.get(status, "—")
            if status == "pending" and note:
                label = f"待续（{note}）"
            elif status == "failed" and note:
                label = f"失败（{note[:40]}）"
            row = self.history_detail.rowCount()
            self.history_detail.insertRow(row)
            self.history_detail.setItem(row, 0, QTableWidgetItem(name))
            self.history_detail.setItem(row, 1, QTableWidgetItem(_STEP_TITLES.get(name, "")))
            self.history_detail.setItem(row, 2, QTableWidgetItem(label))
        # 默认选中第一个未完成步骤
        for row in range(self.history_detail.rowCount()):
            item = self.history_detail.item(row, 0)
            if item is None:
                continue
            status = str((steps.get(item.text()) or {}).get("status") or "")
            if status != "done":
                self.history_detail.selectRow(row)
                break

    def _selected_history_source(self) -> tuple[str, str]:
        item = self.history.currentItem()
        if item is None:
            return "", ""
        data = item.data(Qt.UserRole) or {}
        return str(data.get("key") or ""), str(data.get("url") or data.get("key") or "")

    def _history_resume(self) -> None:
        key, source = self._selected_history_source()
        if not key:
            QMessageBox.information(self, "提示", "先在列表里选一条任务")
            return
        if self.worker is not None and self.worker.isRunning():
            QMessageBox.information(self, "提示", "已有任务在运行")
            return
        from_step = self._first_unfinished_step(key)
        if from_step is None:
            QMessageBox.information(
                self, "提示", "该任务已全部完成；可用「从选中步骤重跑」强制重算某一步"
            )
            return
        self._append_log(f"继续任务 {key}：从 {from_step} 步骤续跑")
        self._start_with(source, from_step)

    def _history_rerun_from_selected(self) -> None:
        key, source = self._selected_history_source()
        if not key:
            QMessageBox.information(self, "提示", "先在列表里选一条任务")
            return
        if self.worker is not None and self.worker.isRunning():
            QMessageBox.information(self, "提示", "已有任务在运行")
            return
        row = self.history_detail.currentRow()
        if row < 0:
            QMessageBox.information(self, "提示", "先在「步骤明细」里选一个步骤")
            return
        item = self.history_detail.item(row, 0)
        step = item.text() if item is not None else ""
        if step not in STEP_ORDER:
            return
        index = STEP_ORDER.index(step)
        affected = "、".join(STEP_ORDER[index:])
        answer = QMessageBox.question(
            self,
            "从该步重跑",
            f"将从「{step}」重跑，以下步骤会重新计算：\n{affected}\n\n继续？",
        )
        if answer != QMessageBox.Yes:
            return
        self._append_log(f"重跑任务 {key}：从 {step} 步骤开始（后续步骤将重算）")
        self._start_with(source, step)

    def _start_with(self, source: str, from_step: str | None) -> None:
        self.url_edit.setText(source)
        index = STEP_ORDER.index(from_step) + 1 if from_step in STEP_ORDER else 0
        self.from_combo.setCurrentIndex(index)
        self.nav.setCurrentRow(0)
        self._start()

    def _load_history_item(self, item: QListWidgetItem) -> None:
        data = item.data(Qt.UserRole) or {}
        self.url_edit.setText(str(data.get("url") or data.get("key") or ""))
        from_step = self._first_unfinished_step(str(data.get("key") or ""))
        if from_step:
            index = STEP_ORDER.index(from_step) + 1  # 0 = 从头开始
            self.from_combo.setCurrentIndex(index)
            self._append_log(f"已载入任务 {data.get('key')}：将从 {from_step} 续跑")
        else:
            self.from_combo.setCurrentIndex(0)
            self._append_log(f"已载入任务 {data.get('key')}（已完成，重跑将全部跳过）")
        self.nav.setCurrentRow(0)

    def _first_unfinished_step(self, key: str) -> str | None:
        state_path = resolve_path(self.settings.paths.work_dir) / key / "state.json"
        if not state_path.exists():
            return None
        try:
            steps = json.loads(state_path.read_text(encoding="utf-8")).get("steps", {})
        except (ValueError, OSError):
            return None
        for name in STEP_ORDER:
            if steps.get(name, {}).get("status") != "done":
                return name
        return None

    # ------------------------------------------------------------------ #
    # 结果与设置
    # ------------------------------------------------------------------ #
    def _output_dir_for(self, key: str) -> Path | None:
        out_root = resolve_path(self.settings.paths.output_dir)
        meta_path = resolve_path(self.settings.paths.work_dir) / key / "meta.json"
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                date = str(meta.get("pubdate") or "")[:10].replace("-", "") or "00000000"
                folder = f"{date}_{key}_{slugify(str(meta.get('title') or 'untitled'))}"
                candidate = out_root / folder
                if candidate.exists():
                    return candidate
            except (ValueError, OSError):
                pass
        return out_root if out_root.exists() else None

    def _current_key(self) -> str:
        key = self.last_key
        if key:
            return key
        item = self.history.currentItem()
        if item is not None:
            return str((item.data(Qt.UserRole) or {}).get("key") or "")
        return ""

    def _current_source(self) -> str:
        """预览/重渲染用的任务来源：最近任务 → 历史选中 → 输入框。"""
        if self.last_key:
            return self.last_key
        item = self.history.currentItem()
        if item is not None:
            data = item.data(Qt.UserRole) or {}
            source = str(data.get("url") or data.get("key") or "")
            if source:
                return source
        return self.url_edit.text().strip()

    def _open_preview(self) -> None:
        source = self._current_source()
        if not source:
            QMessageBox.information(
                self, "提示", "先跑一次任务，或在历史列表选一条，再打开预览"
            )
            return
        PreviewDialog(self.settings, source, self).exec()

    def _open_result(self, *, notes: bool) -> None:
        key = self._current_key()
        if not key:
            QMessageBox.information(self, "提示", "还没有可打开的结果，先跑一次任务")
            return
        base = self._output_dir_for(key)
        if base is None:
            QMessageBox.information(self, "提示", "输出目录还不存在（任务可能还没跑到导出步骤）")
            return
        target = base
        if notes:
            for pattern in ("笔记版-*.md", "原始稿版-*.md", "notes.md", "transcript.raw.md"):
                matches = sorted(base.glob(pattern))
                if matches:
                    target = matches[0]
                    break
        _open_path(target)

    def _open_config_file(self) -> None:
        target = self.settings.config_path or default_config_path()
        if not Path(target).exists():
            QMessageBox.information(self, "提示", f"配置文件还不存在：{target}")
            return
        _open_path(Path(target))

    def _open_logs_dir(self) -> None:
        logs_dir = resolve_path(self.settings.paths.logs_dir)
        if not logs_dir.exists():
            QMessageBox.information(self, "提示", f"日志目录还不存在：{logs_dir}")
            return
        _open_path(logs_dir)

    def _open_settings(self) -> None:
        dialog = SettingsDialog(self.settings, self)
        if dialog.exec() != QDialog.Accepted:
            return
        data = deep_merge(export_settings_dict(self.settings), dialog.patch())
        new_settings = Settings.model_validate(data)
        new_settings.config_path = self.settings.config_path
        target = new_settings.config_path or default_config_path()
        try:
            write_config_dict(target, export_settings_dict(new_settings))
        except OSError as exc:
            QMessageBox.critical(self, "保存失败", f"{target}\n{exc}")
            return
        self.settings = new_settings
        self._append_log(f"配置已保存：{target}")

    # ------------------------------------------------------------------ #
    # 模型页
    # ------------------------------------------------------------------ #
    def refresh_models(self) -> None:
        from ..asr import cuda_available
        from ..model_store import (
            catalog,
            detect_vram_gb,
            models_root,
            recommend_model,
            resolve_repo,
        )

        root = models_root()
        self.model_table.setRowCount(0)
        for spec in catalog():
            folder = resolve_repo(spec.alias).split("/")[-1]
            installed = (root / folder / "model.bin").exists()
            row = self.model_table.rowCount()
            self.model_table.insertRow(row)
            self.model_table.setItem(row, 0, QTableWidgetItem(spec.alias))
            self.model_table.setItem(row, 1, QTableWidgetItem(f"≈{spec.approx_mb / 1024:.1f} GB"))
            self.model_table.setItem(row, 2, QTableWidgetItem(f"≥{spec.vram_gb:.0f} GB"))
            self.model_table.setItem(row, 3, QTableWidgetItem("已安装 ✓" if installed else "—"))
            self.model_table.setItem(row, 4, QTableWidgetItem(spec.note))
        if self.model_table.currentRow() < 0:
            self.model_table.selectRow(0)

        vram = detect_vram_gb()
        spec, reason = recommend_model(vram, has_cuda=cuda_available())
        self.model_banner.setText(f"💡 {reason}　·　模型目录：{root}")

    def _selected_model_alias(self) -> str:
        row = self.model_table.currentRow()
        if row < 0:
            return ""
        item = self.model_table.item(row, 0)
        return item.text() if item is not None else ""

    def _download_selected_model(self) -> None:
        from ..model_store import default_model_dir, resolve_repo

        alias = self._selected_model_alias()
        if not alias:
            QMessageBox.information(self, "提示", "先在表格里选一个模型")
            return
        if self.model_worker is not None and self.model_worker.isRunning():
            QMessageBox.information(self, "提示", "已有下载在进行中")
            return
        target = default_model_dir(alias)
        self.model_progress.setValue(0)
        self.model_status.setText(f"准备下载 {resolve_repo(alias)} → {target}")
        self.model_download_btn.setEnabled(False)
        self.model_cancel_btn.setEnabled(True)
        self._model_progress_state = None

        self.model_worker = ModelDownloadWorker(alias, target)
        self.model_worker.progress.connect(self._on_model_progress)
        self.model_worker.done.connect(self._on_model_done)
        self.model_worker.failed.connect(self._on_model_failed)
        self.model_worker.finished.connect(self._on_model_finished)
        self.model_worker.start()

    def _cancel_model_download(self) -> None:
        if self.model_worker is not None:
            self.model_status.setText("正在取消…（分片已保留，可续传）")
            self.model_cancel_btn.setEnabled(False)
            self.model_worker.cancel()

    def _check_selected_model(self) -> None:
        from ..model_store import check_model, default_model_dir, resolve_repo

        alias = self._selected_model_alias()
        if not alias:
            QMessageBox.information(self, "提示", "先在表格里选一个模型")
            return
        target = default_model_dir(alias)
        if not target.exists():
            self.model_status.setText(f"未安装：{target}（先点「下载所选」）")
            return
        results = check_model(alias, target)
        ok_count = sum(1 for item in results if item.ok)
        if ok_count == len(results):
            self.model_status.setText(f"✓ 校验通过（{ok_count}/{len(results)} 个文件）：{target}")
            return
        bad = "、".join(item.name for item in results if not item.ok)
        self.model_status.setText(f"✗ 校验未通过：{bad}（重新下载会自动修复）")
        QMessageBox.warning(
            self,
            "模型校验",
            f"{resolve_repo(alias)}\n\n缺失或损坏：{bad}\n\n"
            "点「下载所选」重下（分片续传，自动跳过完整文件）。",
        )

    def _on_model_progress(self, name: str, done: int, total: int) -> None:
        import time

        if total > 0:
            self.model_progress.setValue(int(1000 * min(1.0, done / total)))
        now = time.monotonic()
        if self._model_progress_state is not None:
            last_time, last_done = self._model_progress_state
            elapsed = now - last_time
            if elapsed >= 0.5:
                speed = (done - last_done) / elapsed / 1048576
                self.model_status.setText(
                    f"下载 {name}：{done / 1048576:.1f}/{total / 1048576:.1f} MB · {speed:.1f} MB/s"
                )
                self._model_progress_state = (now, done)
        else:
            self._model_progress_state = (now, done)

    def _on_model_done(self, downloaded: int, skipped: int) -> None:
        self.model_status.setText(
            f"✓ 下载完成：新下载 {downloaded} 个文件，跳过已存在 {skipped} 个"
        )
        self.refresh_models()

    def _on_model_failed(self, message: str, hint: str) -> None:
        self.model_status.setText(f"✗ {message}")
        QMessageBox.critical(self, "模型下载失败", message + (f"\n\n{hint}" if hint else ""))

    def _on_model_finished(self) -> None:
        self.model_download_btn.setEnabled(True)
        self.model_cancel_btn.setEnabled(False)


def run_gui(settings: Settings) -> None:
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("VIDAR")
    app.setWindowIcon(theme.app_icon())
    setup_logging(resolve_path(settings.paths.logs_dir), quiet=True)
    window = MainWindow(settings)
    window.show()
    app.exec()


def main() -> None:
    """vidar-gui 入口：无需控制台窗口也能启动。"""
    for stream_name in ("stdout", "stderr"):
        if getattr(sys, stream_name) is None:
            # 进程级占位流：必须存活到进程结束，不能关闭
            setattr(sys, stream_name, open(os.devnull, "w", encoding="utf-8"))  # noqa: SIM115
    settings = load_settings(None)
    run_gui(settings)


if __name__ == "__main__":
    main()

"""VIDAR 桌面 GUI（v0.15 · Clear Glass）。

信息架构：工作台 / 任务 / 队列 / 资料库 / 模型 / 设置。
- 主题：明暗双主题（`[ui].theme = auto|light|dark`），token 在 gui/theme.py
- 耗时操作全部在 QThread：流水线 / 模型下载，经信号回 UI
- 步骤时间线、状态徽章、空状态见 gui/widgets.py
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import subprocess
import sys
import webbrowser
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, QSize, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QColor, QTextOption
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QGridLayout,
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
    QScrollArea,
    QSizePolicy,
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
from .llm_wizard import LlmWizardDialog
from .preview_dialog import PreviewDialog
from .settings_dialog import SettingsDialog
from .widgets import Card, EmptyState, StatusPill, StepTimeline

_STEP_TITLES = {step.name: step.title for step in default_steps()}  # type: ignore[attr-defined]

PAGE_HOME, PAGE_TASK, PAGE_QUEUE, PAGE_LIBRARY, PAGE_MODELS, PAGE_SETTINGS = range(6)

_NAV_ITEMS = (
    ("home", "工作台", "box"),
    ("task", "任务", "play"),
    ("queue", "队列", "list"),
    ("library", "资料库", "book-open"),
    ("models", "模型", "cpu"),
    ("settings", "设置", "settings"),
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
    layout.setContentsMargins(18, 15, 18, 16)
    layout.setSpacing(10)
    return frame, layout


def _hline() -> QFrame:
    line = QFrame()
    line.setFixedHeight(1)
    line.setStyleSheet("background: transparent; border-top: 1px solid palette(mid);")
    return line


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

    def __init__(
        self,
        settings: Settings,
        source: str,
        from_step: str | None,
        key: str | None = None,
    ) -> None:
        super().__init__()
        self._settings = settings
        self._source = source
        self._from_step = from_step
        self._key = key
        self._cancel_requested = False
        self.ctx: RunContext | None = None

    def run(self) -> None:
        try:
            ctx = RunContext(self._settings, self._source, key=self._key)
            self.ctx = ctx
            if self._cancel_requested:  # 启动前就被取消（ctx 尚未创建时的取消意图）
                ctx.cancel()
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
        self._cancel_requested = True
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


class BatchWorker(QThread):
    """批量队列：顺序处理多条来源；转发 batch_* 与流水线事件到 UI。

    on_ctx 持有当前 RunContext，使「停止」可以立即取消正在跑的那一条
    （run_batch 捕获 CancelledError 后停止队列并正常收尾）。
    """

    event = Signal(str, dict)
    done = Signal(object)
    error = Signal(str, str)

    def __init__(self, settings: Settings, sources: list[str]) -> None:
        super().__init__()
        self._settings = settings
        self._sources = list(sources)
        self._ctx: RunContext | None = None
        self._cancel_requested = False

    def run(self) -> None:
        from ..batch import run_batch

        try:
            result = run_batch(
                self._settings,
                self._sources,
                on_event=lambda event, data: self.event.emit(event, data),
                on_ctx=self._keep_ctx,
            )
            self.done.emit(result)
        except VidarError as exc:
            self.error.emit(exc.message, exc.hint)
        except Exception as exc:  # noqa: BLE001 - 兜底，保证 UI 不挂
            self.error.emit(f"未预期错误：{exc}", "")

    def _keep_ctx(self, ctx: object) -> None:
        if isinstance(ctx, RunContext):
            self._ctx = ctx
            if self._cancel_requested:  # 队列刚启动就被取消
                ctx.cancel()

    def cancel(self) -> None:
        self._cancel_requested = True
        if self._ctx is not None:
            self._ctx.cancel()


class MainWindow(QMainWindow):
    def __init__(self, settings: Settings) -> None:
        super().__init__()
        self.settings = settings
        self.theme_name = theme.resolve_theme(settings.ui.theme)
        self.worker: PipelineWorker | None = None
        self.model_worker: ModelDownloadWorker | None = None
        self.batch_worker: BatchWorker | None = None
        self.batch_items: list[dict] = []
        self._batch_running_sources: list[str] = []
        self._batch_launched = 0
        self._batch_done = 0
        self.last_key: str | None = None
        self._pending_key: str | None = None
        self._model_progress_state: tuple[float, int] | None = None
        self._failed_step = ""
        self._last_source = ""
        self._pending_source: str | None = None

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
        self._restore_queue()
        self._sync_recent()
        QTimer.singleShot(80, self.refresh_models)
        self._append_log("就绪：粘贴链接 → 开始。首次跑某条视频会自动解析并下载音频。")
        if not settings.llm.configured and not settings.ui.llm_wizard_done:
            QTimer.singleShot(500, self._open_llm_wizard)

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
        self.pages.addWidget(self._scroll_wrap(self._build_home_page(), 760))
        self.pages.addWidget(self._build_task_page())
        self.pages.addWidget(self._scroll_wrap(self._build_batch_page(), 700))
        self.pages.addWidget(self._scroll_wrap(self._build_library_page(), 720))
        self.pages.addWidget(self._scroll_wrap(self._build_models_page(), 680))
        self.pages.addWidget(self._build_settings_page())
        layout.addWidget(self._build_sidebar())
        layout.addWidget(self.pages, 1)
    def _scroll_wrap(self, body: QWidget, min_height: int) -> QWidget:
        body.setMinimumHeight(min_height)
        page = QWidget()
        page.setObjectName("Page")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidget(body)
        layout.addWidget(scroll)
        return page

    def _build_sidebar(self) -> QWidget:
        side = QFrame()
        side.setObjectName("Sidebar")
        side.setFixedWidth(232)
        layout = QVBoxLayout(side)
        layout.setContentsMargins(14, 18, 14, 14)
        layout.setSpacing(10)

        brand = QHBoxLayout()
        brand.setSpacing(10)
        self.logo_label = QLabel()
        self.logo_label.setFixedSize(36, 36)
        brand.addWidget(self.logo_label)
        text = QVBoxLayout()
        text.setSpacing(0)
        title = QLabel("VIDAR")
        title.setObjectName("LogoTitle")
        sub = QLabel("B 站视频 · 知识文档")
        sub.setObjectName("LogoSub")
        text.addWidget(title)
        text.addWidget(sub)
        brand.addLayout(text, 1)
        layout.addLayout(brand)
        layout.addSpacing(8)

        self.nav = QListWidget()
        self.nav.setObjectName("NavList")
        self.nav.setIconSize(QSize(18, 18))
        self.nav.setFocusPolicy(Qt.NoFocus)
        self.nav.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        for _key, label, _icon_name in _NAV_ITEMS:
            item = QListWidgetItem(label)
            item.setSizeHint(QSize(0, 40))
            self.nav.addItem(item)
        self.nav.setCurrentRow(PAGE_HOME)
        self.nav.currentRowChanged.connect(self.pages.setCurrentIndex)
        self.nav.currentRowChanged.connect(lambda _row: self._refresh_nav_icons())
        layout.addWidget(self.nav, 1)

        env = QFrame()
        env.setObjectName("EnvCard")
        env_layout = QVBoxLayout(env)
        env_layout.setContentsMargins(10, 9, 10, 9)
        env_layout.setSpacing(3)
        env_title = QLabel("本地环境")
        env_title.setObjectName("EnvStrong")
        env_layout.addWidget(env_title)
        self.env_model = QLabel()
        self.env_model.setWordWrap(True)
        env_layout.addWidget(self.env_model)
        self.env_hw = QLabel()
        self.env_hw.setWordWrap(True)
        env_layout.addWidget(self.env_hw)
        layout.addWidget(env)

        self.settings_btn = QPushButton("设置")
        self.settings_btn.setObjectName("Ghost")
        self.settings_btn.setIconSize(QSize(16, 16))
        self.settings_btn.clicked.connect(lambda: self.nav.setCurrentRow(PAGE_SETTINGS))
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

    # ---- 工作台 ----
    def _build_home_page(self) -> QWidget:
        page = QWidget()
        page.setObjectName("Page")
        outer = QHBoxLayout(page)
        outer.setContentsMargins(0, 0, 0, 0)

        body = QWidget()
        body.setMaximumWidth(980)
        body.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        layout = QVBoxLayout(body)
        layout.setContentsMargins(40, 28, 40, 24)
        layout.setSpacing(14)

        kicker = QLabel("VIDAR  ·  工作台")
        kicker.setObjectName("Kicker")
        layout.addWidget(kicker)
        head = QLabel("把视频，变成可读的文档")
        head.setStyleSheet("font-size: 30px; font-weight: 650; letter-spacing: -0.5px;")
        layout.addWidget(head)
        sub = QLabel(
            "粘贴一条 B 站链接，自动完成解析、语音识别、文稿精修与排版，"
            "产出 Markdown / PDF / EPUB。"
        )
        sub.setObjectName("PageSub")
        sub.setWordWrap(True)
        layout.addWidget(sub)
        layout.addSpacing(6)

        input_card = QFrame()
        input_card.setObjectName("Card")
        card_layout = QVBoxLayout(input_card)
        card_layout.setContentsMargins(16, 14, 16, 12)
        card_layout.setSpacing(10)
        self.url_edit = QLineEdit()
        self.url_edit.setPlaceholderText("粘贴 BV 号或视频链接，例如 BV1xx411c7mD")
        self.url_edit.setMinimumHeight(44)
        self.url_edit.setStyleSheet("font-size: 14px; padding: 10px 14px;")
        self.url_edit.returnPressed.connect(self._start)
        self.url_edit.textEdited.connect(self._clear_pending_key)
        card_layout.addWidget(self.url_edit)

        row = QHBoxLayout()
        row.setSpacing(8)
        self.from_combo = QComboBox()
        self.from_combo.addItem("从头开始", None)
        for name in STEP_ORDER:
            self.from_combo.addItem(f"从「{_STEP_TITLES.get(name, name)}」重跑", name)
        self.start_btn = QPushButton("开始识别")
        self.start_btn.setObjectName("Primary")
        self.start_btn.setIconSize(QSize(15, 15))
        self.start_btn.setMinimumHeight(36)
        row.addWidget(self.from_combo)
        row.addStretch(1)
        row.addWidget(self.start_btn)
        card_layout.addLayout(row)

        hint = QLabel("支持 BV 号、视频链接与分 P；回车即可开始 · 失败可从任意步骤续跑")
        hint.setObjectName("Faint")
        card_layout.addWidget(hint)
        layout.addWidget(input_card)

        chips = QHBoxLayout()
        chips.setSpacing(8)
        for text in ("Markdown / PDF / EPUB", "本地识别 · 不上传音频", "断点续跑 · 暂停释放显存"):
            chip = QLabel(text)
            chip.setObjectName("Chip")
            chips.addWidget(chip)
        chips.addStretch(1)
        layout.addLayout(chips)

        recent = Card("最近任务", "")
        all_btn = QPushButton("查看全部")
        all_btn.setObjectName("Ghost")
        all_btn.clicked.connect(lambda: self.nav.setCurrentRow(PAGE_LIBRARY))
        recent.add_header_widget(all_btn)
        self.recent_list = QListWidget()
        self.recent_list.setObjectName("HistoryList")
        self.recent_list.setFixedHeight(150)
        self.recent_list.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.recent_list.setTextElideMode(Qt.ElideRight)
        self.recent_list.setWordWrap(False)
        self.recent_list.itemDoubleClicked.connect(self._open_recent_item)
        recent.body.addWidget(self.recent_list)
        self.recent_empty = EmptyState("clock", "还没有任务", "粘贴第一条链接，结果会出现在这里")
        recent.body.addWidget(self.recent_empty)
        layout.addWidget(recent)

        quick = QGridLayout()
        quick.setSpacing(12)
        model_card = Card("语音模型", "管理")
        self.home_model_label = QLabel("检测中…")
        self.home_model_label.setObjectName("Muted")
        model_card.body.addWidget(self.home_model_label)
        model_btn = QPushButton("打开模型页")
        model_btn.setObjectName("Ghost")
        model_btn.clicked.connect(lambda: self.nav.setCurrentRow(PAGE_MODELS))
        model_card.body.addWidget(model_btn, 0, Qt.AlignLeft)

        llm_card = Card("大模型", "配置")
        self.home_llm_label = QLabel("未配置")
        self.home_llm_label.setObjectName("Muted")
        llm_card.body.addWidget(self.home_llm_label)
        llm_btn = QPushButton("接入向导")
        llm_btn.setObjectName("Ghost")
        llm_btn.clicked.connect(self._open_llm_wizard)
        llm_card.body.addWidget(llm_btn, 0, Qt.AlignLeft)
        quick.addWidget(model_card, 0, 0)
        quick.addWidget(llm_card, 0, 1)
        layout.addLayout(quick)
        layout.addStretch(1)

        outer.addWidget(body)

        self.start_btn.clicked.connect(self._start)
        return page

    # ---- 任务 ----
    def _build_task_page(self) -> QWidget:
        page = QWidget()
        page.setObjectName("Page")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(20, 14, 20, 14)
        layout.setSpacing(10)

        top = QHBoxLayout()
        crumb = QLabel("工作台  ›  任务")
        crumb.setObjectName("Faint")
        top.addWidget(crumb)
        top.addStretch(1)
        self.task_chip_model = QLabel("CUDA · large-v3")
        self.task_chip_model.setObjectName("Chip")
        self.task_chip_mode = QLabel("本地识别")
        self.task_chip_mode.setObjectName("Chip")
        top.addWidget(self.task_chip_mode)
        top.addWidget(self.task_chip_model)
        new_btn = QPushButton("新建任务")
        new_btn.setObjectName("Ghost")
        new_btn.clicked.connect(lambda: self.nav.setCurrentRow(PAGE_HOME))
        top.addWidget(new_btn)
        layout.addLayout(top)

        hero = QHBoxLayout()
        left = QVBoxLayout()
        left.setSpacing(4)
        kicker_row = QHBoxLayout()
        self.task_pill = StatusPill("就绪", "pending")
        self.task_kicker = QLabel("等待开始")
        self.task_kicker.setObjectName("Faint")
        kicker_row.addWidget(self.task_pill)
        kicker_row.addWidget(self.task_kicker)
        kicker_row.addStretch(1)
        left.addLayout(kicker_row)
        self.task_title = QLabel("还没有运行中的任务")
        self.task_title.setObjectName("HeroTitle")
        self.task_title.setWordWrap(True)
        left.addWidget(self.task_title)
        self.task_meta = QLabel("在「工作台」粘贴链接开始；结果会自动保存到资料库")
        self.task_meta.setObjectName("PageSub")
        left.addWidget(self.task_meta)
        hero.addLayout(left, 1)
        layout.addLayout(hero)

        # 失败卡片（默认隐藏）
        self.error_card = QFrame()
        self.error_card.setObjectName("ErrorCard")
        error_layout = QVBoxLayout(self.error_card)
        error_layout.setContentsMargins(14, 11, 14, 11)
        error_layout.setSpacing(5)
        error_head = QLabel("任务失败")
        error_head.setObjectName("ErrorTitle")
        error_layout.addWidget(error_head)
        self.error_message = QLabel("")
        self.error_message.setWordWrap(True)
        error_layout.addWidget(self.error_message)
        self.error_hint = QLabel("")
        self.error_hint.setObjectName("Muted")
        self.error_hint.setWordWrap(True)
        error_layout.addWidget(self.error_hint)
        error_actions = QHBoxLayout()
        self.retry_failed_btn = QPushButton("从失败步骤重跑")
        self.retry_failed_btn.setObjectName("Soft")
        self.retry_failed_btn.clicked.connect(self._retry_failed)
        self.copy_error_btn = QPushButton("复制错误")
        self.copy_error_btn.setObjectName("Ghost")
        self.copy_error_btn.clicked.connect(self._copy_error)
        self.fix_model_btn = QPushButton("修复模型配置")
        self.fix_model_btn.setObjectName("Ghost")
        self.fix_model_btn.clicked.connect(self._repair_from_error)
        error_actions.addWidget(self.retry_failed_btn)
        error_actions.addWidget(self.fix_model_btn)
        error_actions.addStretch(1)
        error_actions.addWidget(self.copy_error_btn)
        error_layout.addLayout(error_actions)
        self.error_card.setVisible(False)
        layout.addWidget(self.error_card)

        content_widget = QWidget()
        content_widget.setObjectName("Page")
        content = QHBoxLayout(content_widget)
        content.setContentsMargins(0, 0, 0, 0)
        content.setSpacing(14)
        steps_card = Card("流程")
        self.steps_table = StepTimeline(
            [(name, _STEP_TITLES.get(name, name)) for name in STEP_ORDER]
        )
        steps_card.body.addWidget(self.steps_table)
        steps_card.setFixedWidth(288)
        content.addWidget(steps_card)

        right = QVBoxLayout()
        right.setSpacing(12)
        log_card = Card("运行日志")
        self.log_view = QPlainTextEdit()
        self.log_view.setObjectName("LogView")
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(5000)
        self.log_view.setMinimumHeight(150)
        self.log_view.setWordWrapMode(QTextOption.WrapAnywhere)
        log_card.body.addWidget(self.log_view, 1)
        log_foot = QHBoxLayout()
        self.log_foot_label = QLabel("日志实时追加")
        self.log_foot_label.setObjectName("Faint")
        log_foot.addWidget(self.log_foot_label)
        log_foot.addStretch(1)
        copy_log_btn = QPushButton("复制")
        copy_log_btn.setObjectName("Ghost")
        copy_log_btn.clicked.connect(self._copy_log)
        log_foot.addWidget(copy_log_btn)
        log_card.body.addLayout(log_foot)
        right.addWidget(log_card, 1)

        out_card = Card("产物", "完成后自动生成")
        files = QHBoxLayout()
        files.setSpacing(8)
        for icon_name, name, state in (
            ("file-text", "Markdown", "待生成"),
            ("file-text", "PDF 排版", "待生成"),
            ("book-open", "EPUB", "待生成"),
        ):
            chip = QFrame()
            chip.setObjectName("Chip")
            chip_layout = QHBoxLayout(chip)
            chip_layout.setContentsMargins(9, 6, 10, 6)
            chip_layout.setSpacing(7)
            icon_label = QLabel()
            icon_label.setPixmap(
                theme.icon(icon_name, theme.palette("light")["text_faint"], size=15).pixmap(15, 15)
            )
            chip_layout.addWidget(icon_label)
            text = QLabel(f"<b>{name}</b>　<span style='color:#A49DB6'>{state}</span>")
            chip_layout.addWidget(text)
            files.addWidget(chip)
        out_card.body.addLayout(files)
        out_actions = QHBoxLayout()
        self.open_out_btn = QPushButton("打开输出目录")
        self.open_notes_btn = QPushButton("打开文稿")
        self.preview_btn = QPushButton("预览排版")
        for button in (self.open_out_btn, self.open_notes_btn, self.preview_btn):
            button.setObjectName("Ghost")
            button.setIconSize(QSize(15, 15))
            out_actions.addWidget(button)
        out_actions.addStretch(1)
        out_card.body.addLayout(out_actions)
        right.addWidget(out_card)
        content.addLayout(right, 1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidget(content_widget)
        layout.addWidget(scroll, 1)

        bar = QFrame()
        bar.setObjectName("ActionBar")
        bar_layout = QHBoxLayout(bar)
        bar_layout.setContentsMargins(16, 9, 12, 9)
        bar_layout.setSpacing(10)
        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setValue(0)
        self.progress.setFixedWidth(210)
        self.progress.setTextVisible(False)
        self.status_label = QLabel("就绪")
        self.status_label.setObjectName("Muted")
        bar_layout.addWidget(self.progress)
        bar_layout.addWidget(self.status_label, 1)
        self.pause_btn = QPushButton("暂停")
        self.pause_btn.setObjectName("Soft")
        self.pause_btn.setVisible(False)
        self.resume_btn = QPushButton("继续")
        self.resume_btn.setObjectName("Soft")
        self.resume_btn.setVisible(False)
        self.release_btn = QPushButton("释放显存")
        self.release_btn.setObjectName("Ghost")
        self.release_btn.setVisible(False)
        self.cancel_btn = QPushButton("取消")
        self.cancel_btn.setObjectName("Danger")
        self.cancel_btn.setEnabled(False)
        for button in (self.pause_btn, self.resume_btn, self.release_btn, self.cancel_btn):
            button.setIconSize(QSize(15, 15))
            bar_layout.addWidget(button)
        layout.addWidget(bar)

        self.cancel_btn.clicked.connect(self._cancel)
        self.pause_btn.clicked.connect(self._pause)
        self.resume_btn.clicked.connect(self._resume)
        self.release_btn.clicked.connect(self._release_resources)
        self.open_out_btn.clicked.connect(lambda: self._open_result(notes=False))
        self.open_notes_btn.clicked.connect(lambda: self._open_result(notes=True))
        self.preview_btn.clicked.connect(self._open_preview)
        return page

    # ---- 队列 ----
    def _build_batch_page(self) -> QWidget:
        page = QWidget()
        page.setObjectName("Page")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(26, 16, 26, 16)
        layout.setSpacing(10)

        top = QHBoxLayout()
        left = QVBoxLayout()
        left.setSpacing(2)
        head = QLabel("队列")
        head.setObjectName("PageTitle")
        left.addWidget(head)
        sub = QLabel("多条链接顺序处理：一条失败不影响下一条；每条都支持断点续跑")
        sub.setObjectName("PageSub")
        left.addWidget(sub)
        top.addLayout(left, 1)
        self.batch_count_label = QLabel("0 个任务")
        self.batch_count_label.setObjectName("Chip")
        top.addWidget(self.batch_count_label, 0, Qt.AlignBottom)
        layout.addLayout(top)

        input_card = Card("添加任务", "每行一个，可带 # 注释")
        self.batch_input = QPlainTextEdit()
        self.batch_input.setPlaceholderText(
            "BV1xx411c7mD\nhttps://www.bilibili.com/video/BV1xx411c7mD\n# 这行是注释"
        )
        self.batch_input.setFixedHeight(76)
        input_card.body.addWidget(self.batch_input)
        row = QHBoxLayout()
        self.batch_add_btn = QPushButton("加入队列")
        self.batch_add_btn.setObjectName("Primary")
        self.batch_import_btn = QPushButton("从文件导入")
        self.batch_clear_btn = QPushButton("清空列表")
        self.batch_clear_btn.setObjectName("Ghost")
        row.addWidget(self.batch_add_btn)
        row.addWidget(self.batch_import_btn)
        row.addStretch(1)
        row.addWidget(self.batch_clear_btn)
        input_card.body.addLayout(row)
        layout.addWidget(input_card)

        queue_card = Card("任务列表", "")
        self.batch_table = QTableWidget(0, 4)
        self.batch_table.setHorizontalHeaderLabels(["#", "任务", "状态", "说明"])
        header = self.batch_table.horizontalHeader()
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(3, QHeaderView.Stretch)
        header.setSectionResizeMode(0, QHeaderView.Fixed)
        self.batch_table.setColumnWidth(0, 44)
        self.batch_table.setColumnWidth(2, 120)
        self.batch_table.horizontalHeader().setStretchLastSection(True)
        self.batch_table.horizontalHeader().setDefaultAlignment(
            Qt.AlignLeft | Qt.AlignVCenter
        )
        self.batch_table.verticalHeader().setVisible(False)
        self.batch_table.verticalHeader().setDefaultSectionSize(40)
        self.batch_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.batch_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.batch_table.setSelectionMode(QTableWidget.SingleSelection)
        self.batch_table.setShowGrid(False)
        queue_card.body.addWidget(self.batch_table, 1)
        self.queue_empty = EmptyState(
            "list", "队列为空", "粘贴链接后点「加入队列」，多条会顺序执行"
        )
        queue_card.body.addWidget(self.queue_empty)
        self.batch_table.setVisible(False)
        layout.addWidget(queue_card, 1)

        actions = QHBoxLayout()
        self.batch_start_btn = QPushButton("开始队列")
        self.batch_start_btn.setObjectName("Primary")
        self.batch_stop_btn = QPushButton("停止")
        self.batch_stop_btn.setObjectName("Danger")
        self.batch_stop_btn.setEnabled(False)
        self.batch_retry_btn = QPushButton("重试失败项")
        self.batch_remove_btn = QPushButton("移除所选")
        self.batch_remove_btn.setObjectName("Ghost")
        for button in (
            self.batch_start_btn,
            self.batch_stop_btn,
            self.batch_retry_btn,
            self.batch_remove_btn,
        ):
            button.setIconSize(QSize(15, 15))
            actions.addWidget(button)
        actions.addStretch(1)
        self.batch_progress = QProgressBar()
        self.batch_progress.setRange(0, 1000)
        self.batch_progress.setValue(0)
        self.batch_progress.setFixedWidth(160)
        actions.addWidget(self.batch_progress)
        self.batch_status = QLabel("队列为空")
        self.batch_status.setObjectName("Faint")
        actions.addWidget(self.batch_status)
        layout.addLayout(actions)

        log_card = Card("队列日志")
        self.batch_log_view = QPlainTextEdit()
        self.batch_log_view.setObjectName("LogView")
        self.batch_log_view.setReadOnly(True)
        self.batch_log_view.setMaximumBlockCount(2000)
        self.batch_log_view.setFixedHeight(96)
        self.batch_log_view.setWordWrapMode(QTextOption.WrapAnywhere)
        log_card.body.addWidget(self.batch_log_view)
        layout.addWidget(log_card)

        self.batch_add_btn.clicked.connect(self._batch_add)
        self.batch_import_btn.clicked.connect(self._batch_import)
        self.batch_clear_btn.clicked.connect(self._batch_clear)
        self.batch_start_btn.clicked.connect(self._batch_start)
        self.batch_stop_btn.clicked.connect(self._batch_stop)
        self.batch_retry_btn.clicked.connect(self._batch_retry_failed)
        self.batch_remove_btn.clicked.connect(self._batch_remove_selected)
        return page

    # ---- 资料库 ----
    def _build_library_page(self) -> QWidget:
        page = QWidget()
        page.setObjectName("Page")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(26, 16, 26, 16)
        layout.setSpacing(10)

        top = QHBoxLayout()
        left = QVBoxLayout()
        left.setSpacing(2)
        head = QLabel("资料库")
        head.setObjectName("PageTitle")
        left.addWidget(head)
        sub = QLabel("每个任务都记住进度：继续未完成的，或从任意步骤重跑")
        sub.setObjectName("PageSub")
        left.addWidget(sub)
        top.addLayout(left, 1)
        layout.addLayout(top)

        filters = QHBoxLayout()
        self.history_search = QLineEdit()
        self.history_search.setPlaceholderText("搜索标题、BV 号或目录名")
        self.history_search.setClearButtonEnabled(True)
        self.history_search.textChanged.connect(self._filter_history)
        self.history_filter = QComboBox()
        self.history_filter.addItem("全部状态", "all")
        self.history_filter.addItem("已完成", "done")
        self.history_filter.addItem("有失败", "failed")
        self.history_filter.addItem("未完成", "open")
        self.history_filter.currentIndexChanged.connect(self._filter_history)
        self.history_refresh_btn = QPushButton("刷新")
        self.history_refresh_btn.setObjectName("Ghost")
        self.history_refresh_btn.setIconSize(QSize(15, 15))
        filters.addWidget(self.history_search, 1)
        filters.addWidget(self.history_filter)
        filters.addWidget(self.history_refresh_btn)
        layout.addLayout(filters)

        content = QHBoxLayout()
        content.setSpacing(12)
        list_card = Card("全部任务", "")
        list_card.setFixedWidth(360)
        self.history = QListWidget()
        self.history.setObjectName("HistoryList")
        self.history.itemDoubleClicked.connect(self._load_history_item)
        self.history.currentItemChanged.connect(self._on_history_selected)
        list_card.body.addWidget(self.history, 1)
        self.history_empty = EmptyState(
            "book-open", "资料库还是空的", "完成第一条视频后，产物与进度会出现在这里"
        )
        list_card.body.addWidget(self.history_empty)
        content.addWidget(list_card)

        detail_card = Card("任务详情", "选中任务查看步骤")
        self.history_title = QLabel("未选择任务")
        self.history_title.setStyleSheet("font-size: 15px; font-weight: 650;")
        self.history_title.setWordWrap(True)
        detail_card.body.addWidget(self.history_title)
        self.history_meta = QLabel("")
        self.history_meta.setObjectName("Muted")
        self.history_meta.setWordWrap(True)
        detail_card.body.addWidget(self.history_meta)
        self.history_detail = QTableWidget(0, 3)
        self.history_detail.setHorizontalHeaderLabels(["步骤", "标识", "状态"])
        detail_header = self.history_detail.horizontalHeader()
        detail_header.setSectionResizeMode(0, QHeaderView.Stretch)
        detail_header.setSectionResizeMode(1, QHeaderView.Fixed)
        detail_header.setSectionResizeMode(2, QHeaderView.Fixed)
        self.history_detail.setColumnWidth(1, 92)
        self.history_detail.setColumnWidth(2, 78)
        self.history_detail.setColumnHidden(1, True)
        self.history_detail.horizontalHeader().setDefaultAlignment(
            Qt.AlignLeft | Qt.AlignVCenter
        )
        self.history_detail.verticalHeader().setVisible(False)
        self.history_detail.verticalHeader().setDefaultSectionSize(36)
        self.history_detail.setEditTriggers(QTableWidget.NoEditTriggers)
        self.history_detail.setSelectionBehavior(QTableWidget.SelectRows)
        self.history_detail.setSelectionMode(QTableWidget.SingleSelection)
        self.history_detail.setShowGrid(False)
        detail_card.body.addWidget(self.history_detail, 1)
        self.detail_empty = EmptyState("list", "未选择任务", "从左侧选择一条任务，查看步骤与产物")
        detail_card.body.addWidget(self.detail_empty)
        self.history_detail.setVisible(False)
        actions_top = QHBoxLayout()
        actions_bottom = QHBoxLayout()
        self.history_resume_btn = QPushButton("继续")
        self.history_resume_btn.setObjectName("Primary")
        self.history_resume_btn.setToolTip("从第一个未完成步骤继续")
        self.history_rerun_btn = QPushButton("重跑此步")
        self.history_rerun_btn.setToolTip("从步骤明细中选中的步骤重跑")
        self.history_open_out_btn = QPushButton("目录")
        self.history_open_out_btn.setToolTip("打开输出目录")
        self.history_open_notes_btn = QPushButton("文稿")
        self.history_open_notes_btn.setToolTip("打开 Markdown 文稿")
        self.history_preview_btn = QPushButton("预览")
        self.history_preview_btn.setToolTip("预览 PDF / EPUB 排版")
        for button in (self.history_resume_btn, self.history_rerun_btn):
            button.setIconSize(QSize(15, 15))
            actions_top.addWidget(button)
        for button in (
            self.history_open_out_btn,
            self.history_open_notes_btn,
            self.history_preview_btn,
        ):
            button.setIconSize(QSize(15, 15))
            actions_bottom.addWidget(button)
        detail_card.body.addLayout(actions_top)
        detail_card.body.addLayout(actions_bottom)
        content.addWidget(detail_card, 1)
        layout.addLayout(content, 1)

        self.history_resume_btn.clicked.connect(self._history_resume)
        self.history_rerun_btn.clicked.connect(self._history_rerun_from_selected)
        self.history_open_out_btn.clicked.connect(
            lambda: self._open_result(notes=False, prefer_history=True)
        )
        self.history_open_notes_btn.clicked.connect(
            lambda: self._open_result(notes=True, prefer_history=True)
        )
        self.history_preview_btn.clicked.connect(lambda: self._open_preview(prefer_history=True))
        self.history_refresh_btn.clicked.connect(self.refresh_history)
        return page

    # ---- 模型 ----
    def _build_models_page(self) -> QWidget:
        page = QWidget()
        page.setObjectName("Page")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(26, 16, 26, 16)
        layout.setSpacing(10)

        head = QLabel("模型")
        head.setObjectName("PageTitle")
        layout.addWidget(head)
        sub = QLabel("按显存推荐档位；下载、校验、修复一步到位")
        sub.setObjectName("PageSub")
        layout.addWidget(sub)

        self.model_banner = QLabel("正在检测显卡…")
        self.model_banner.setObjectName("Banner")
        self.model_banner.setWordWrap(True)
        self.model_banner.setContentsMargins(12, 10, 12, 10)
        layout.addWidget(self.model_banner)

        card = Card("模型目录", "")
        self.model_table = QTableWidget(0, 5)
        self.model_table.setHorizontalHeaderLabels(["型号", "大小", "显存建议", "状态", "说明"])
        header = self.model_table.horizontalHeader()
        header.setSectionResizeMode(4, QHeaderView.Stretch)
        header.setSectionResizeMode(0, QHeaderView.Fixed)
        header.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.model_table.setColumnWidth(0, 132)
        self.model_table.verticalHeader().setVisible(False)
        self.model_table.verticalHeader().setDefaultSectionSize(40)
        self.model_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.model_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.model_table.setSelectionMode(QTableWidget.SingleSelection)
        self.model_table.setShowGrid(False)
        card.body.addWidget(self.model_table, 1)
        row = QHBoxLayout()
        self.model_download_btn = QPushButton("下载所选")
        self.model_download_btn.setObjectName("Primary")
        self.model_cancel_btn = QPushButton("取消")
        self.model_cancel_btn.setObjectName("Danger")
        self.model_cancel_btn.setEnabled(False)
        self.model_check_btn = QPushButton("校验")
        self.model_repair_btn = QPushButton("修复特征配置")
        self.model_repair_btn.setObjectName("Soft")
        self.model_refresh_btn = QPushButton("刷新")
        self.model_refresh_btn.setObjectName("Ghost")
        for button in (
            self.model_download_btn,
            self.model_cancel_btn,
            self.model_check_btn,
            self.model_repair_btn,
            self.model_refresh_btn,
        ):
            button.setIconSize(QSize(15, 15))
            row.addWidget(button)
        row.addStretch(1)
        card.body.addLayout(row)
        progress_row = QHBoxLayout()
        self.model_progress = QProgressBar()
        self.model_progress.setRange(0, 1000)
        self.model_progress.setValue(0)
        self.model_status = QLabel("未开始")
        self.model_status.setObjectName("Muted")
        progress_row.addWidget(self.model_progress, 1)
        progress_row.addWidget(self.model_status)
        card.body.addLayout(progress_row)
        layout.addWidget(card, 1)

        self.model_disk_label = QLabel("")
        self.model_disk_label.setObjectName("Faint")
        layout.addWidget(self.model_disk_label)

        self.model_download_btn.clicked.connect(self._download_selected_model)
        self.model_cancel_btn.clicked.connect(self._cancel_model_download)
        self.model_check_btn.clicked.connect(self._check_selected_model)
        self.model_repair_btn.clicked.connect(self._repair_selected_model)
        self.model_refresh_btn.clicked.connect(self.refresh_models)
        return page

    # ---- 设置 ----
    def _build_settings_page(self) -> QWidget:
        page = QWidget()
        page.setObjectName("Page")
        outer = QVBoxLayout(page)
        outer.setContentsMargins(26, 16, 26, 16)
        outer.setSpacing(8)

        head = QLabel("设置")
        head.setObjectName("PageTitle")
        outer.addWidget(head)
        sub = QLabel("外观、识别、大模型与导出；所有配置都写回本机 config.toml")
        sub.setObjectName("PageSub")
        outer.addWidget(sub)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        body.setObjectName("Page")
        layout = QVBoxLayout(body)
        layout.setContentsMargins(0, 6, 6, 6)
        layout.setSpacing(12)

        # 外观
        look = Card("外观", "")
        row = QHBoxLayout()
        self.theme_buttons: dict[str, QPushButton] = {}
        for value, label in (("light", "浅色"), ("dark", "深色"), ("auto", "跟随系统")):
            button = QPushButton(label)
            button.clicked.connect(lambda _=False, v=value: self._set_theme_value(v))
            self.theme_buttons[value] = button
            row.addWidget(button)
        row.addStretch(1)
        look.body.addLayout(row)
        layout.addWidget(look)

        # 识别
        asr = Card("语音识别", "改动后下次任务生效")
        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(8)
        grid.addWidget(QLabel("模型"), 0, 0)
        self.set_asr_model = QLineEdit(self.settings.asr.model)
        grid.addWidget(self.set_asr_model, 0, 1)
        grid.addWidget(QLabel("设备"), 0, 2)
        self.set_asr_device = QComboBox()
        for value in ("auto", "cuda", "cpu"):
            self.set_asr_device.addItem(value)
        self.set_asr_device.setCurrentText(self.settings.asr.device)
        grid.addWidget(self.set_asr_device, 0, 3)
        grid.addWidget(QLabel("HF 镜像"), 1, 0)
        self.set_hf_endpoint = QLineEdit(self.settings.asr.hf_endpoint)
        self.set_hf_endpoint.setPlaceholderText("留空使用官方源")
        grid.addWidget(self.set_hf_endpoint, 1, 1, 1, 3)
        grid.setColumnStretch(1, 1)
        asr.body.addLayout(grid)
        save_asr = QPushButton("保存识别设置")
        save_asr.setObjectName("Soft")
        save_asr.clicked.connect(self._save_asr_settings)
        asr.body.addWidget(save_asr, 0, Qt.AlignLeft)
        layout.addWidget(asr)

        # 大模型
        llm = Card("大模型", "")
        self.llm_summary = QLabel("")
        self.llm_summary.setObjectName("Muted")
        self.llm_summary.setWordWrap(True)
        llm.body.addWidget(self.llm_summary)
        llm_row = QHBoxLayout()
        self.llm_test_btn = QPushButton("测试连接")
        self.llm_test_label = QLabel("")
        self.llm_test_label.setObjectName("Muted")
        self.llm_test_btn.clicked.connect(self._test_llm_connection)
        self.wizard_btn = QPushButton("接入向导")
        self.wizard_btn.setObjectName("Primary")
        edit_llm = QPushButton("完整配置")
        edit_llm.clicked.connect(self._open_settings)
        llm_row.addWidget(self.wizard_btn)
        llm_row.addWidget(self.llm_test_btn)
        llm_row.addWidget(edit_llm)
        llm_row.addWidget(self.llm_test_label, 1)
        llm_row.addStretch(1)
        llm.body.addLayout(llm_row)
        layout.addWidget(llm)

        # 导出
        export = Card("导出", "默认导出格式")
        row1 = QHBoxLayout()
        self.set_pdf = QCheckBox("PDF")
        self.set_pdf.setChecked(self.settings.export.pdf)
        self.set_docx = QCheckBox("Word")
        self.set_docx.setChecked(self.settings.export.docx)
        self.set_epub = QCheckBox("EPUB")
        self.set_epub.setChecked(self.settings.export.epub)
        for box in (self.set_pdf, self.set_docx, self.set_epub):
            row1.addWidget(box)
        row1.addStretch(1)
        export.body.addLayout(row1)
        row2 = QHBoxLayout()
        self.set_pdf_style = QComboBox()
        self.set_pdf_style.addItem("报刊", "newspaper")
        self.set_pdf_style.addItem("论文", "paper")
        self._ensure_combo_value(self.set_pdf_style, self.settings.export.pdf_style)
        self.set_pdf_mode = QComboBox()
        self.set_pdf_mode.addItem("全文", "full")
        self.set_pdf_mode.addItem("精简", "brief")
        self._ensure_combo_value(self.set_pdf_mode, self.settings.export.pdf_mode)
        self.set_pdf_scale = QComboBox()
        for label, value in (("紧凑", "compact"), ("标准", "standard"), ("大", "large")):
            self.set_pdf_scale.addItem(label, value)
        self._ensure_combo_value(self.set_pdf_scale, self.settings.export.pdf_scale)
        row2.addWidget(QLabel("PDF 风格"))
        row2.addWidget(self.set_pdf_style)
        row2.addWidget(QLabel("篇幅"))
        row2.addWidget(self.set_pdf_mode)
        row2.addWidget(QLabel("字号"))
        row2.addWidget(self.set_pdf_scale)
        row2.addStretch(1)
        export.body.addLayout(row2)
        save_export = QPushButton("保存导出设置")
        save_export.setObjectName("Soft")
        save_export.clicked.connect(self._save_export_settings)
        export.body.addWidget(save_export, 0, Qt.AlignLeft)
        layout.addWidget(export)

        # 关于
        about = Card("关于", f"v{__version__}")
        about_row = QHBoxLayout()
        self.about_icon = QLabel()
        self.about_icon.setFixedSize(56, 56)
        about_row.addWidget(self.about_icon)
        about_text = QVBoxLayout()
        about_text.setSpacing(3)
        tagline = QLabel(
            "本地语音识别（faster-whisper）+ 大模型精修/摘要/翻译，"
            "产出 Markdown / PDF / Word / EPUB。"
        )
        tagline.setWordWrap(True)
        tagline.setObjectName("Muted")
        about_text.addWidget(tagline)
        links = QHBoxLayout()
        for label, handler in (
            ("项目主页", lambda: webbrowser.open("https://github.com/GinyvaXu/VIDAR")),
            ("配置文件", self._open_config_file),
            ("日志目录", self._open_logs_dir),
            ("输出目录", lambda: self._open_result(notes=False)),
        ):
            button = QPushButton(label)
            button.setObjectName("Ghost")
            button.clicked.connect(handler)
            links.addWidget(button)
        links.addStretch(1)
        about_text.addLayout(links)
        about_row.addLayout(about_text, 1)
        about.body.addLayout(about_row)
        layout.addWidget(about)

        layout.addStretch(1)
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)
        return page

    # ------------------------------------------------------------------ #
    # 主题
    # ------------------------------------------------------------------ #
    def _apply_theme(self) -> None:
        app = QApplication.instance()
        if app is not None:
            app.setStyleSheet(theme.build_qss(self.theme_name))
            app.setProperty("vidar_theme", self.theme_name)
        colors = theme.palette(self.theme_name)
        self._refresh_nav_icons()

        button_icons = {
            "start_btn": "rocket",
            "cancel_btn": "square",
            "pause_btn": "pause",
            "resume_btn": "play",
            "release_btn": "hard-drive",
            "open_out_btn": "folder-open",
            "open_notes_btn": "file-text",
            "preview_btn": "eye",
            "batch_start_btn": "play",
            "batch_stop_btn": "square",
            "batch_retry_btn": "refresh-cw",
            "batch_remove_btn": "x",
            "batch_add_btn": "list",
            "batch_import_btn": "folder-open",
            "batch_clear_btn": "x",
            "history_resume_btn": "play",
            "history_rerun_btn": "refresh-cw",
            "history_open_out_btn": "folder-open",
            "history_open_notes_btn": "file-text",
            "history_preview_btn": "eye",
            "history_refresh_btn": "refresh-cw",
            "model_download_btn": "download",
            "model_cancel_btn": "x",
            "model_check_btn": "gauge",
            "model_repair_btn": "refresh-cw",
            "model_refresh_btn": "refresh-cw",
            "wizard_btn": "sparkles",
            "settings_btn": "settings",
        }
        for attr, icon_name in button_icons.items():
            button = getattr(self, attr, None)
            if button is None:
                continue
            color = "#FFFFFF" if button.objectName() == "Primary" else colors["text_muted"]
            button.setIcon(theme.icon(icon_name, color, size=16))

        self.theme_btn.setText("深色模式" if self.theme_name == "light" else "浅色模式")
        self.theme_btn.setIcon(
            theme.icon(
                "moon" if self.theme_name == "light" else "sun", colors["text_muted"], size=16
            )
        )
        self.logo_label.setPixmap(theme.app_icon().pixmap(36, 36))
        self.about_icon.setPixmap(theme.app_icon().pixmap(56, 56))
        self.task_pill.apply_theme()
        self.steps_table.apply_theme()
        if hasattr(self, "batch_table"):
            self._refresh_batch_table(persist=False)
        for attr in ("queue_empty", "history_empty", "recent_empty", "detail_empty"):
            widget = getattr(self, attr, None)
            if widget is not None:
                widget.apply_theme()
        for value, button in getattr(self, "theme_buttons", {}).items():
            active = (value == self.theme_name and self.settings.ui.theme != "auto") or (
                value == "auto" and self.settings.ui.theme == "auto"
            )
            button.setObjectName("Primary" if active else "")
            button.style().unpolish(button)
            button.style().polish(button)
        model_name = Path(self.settings.asr.model).name or self.settings.asr.model
        self.env_model.setText(f"模型 {model_name}")
        if not self.env_hw.text():
            self.env_hw.setText("设备检测中…")
        self.home_model_label.setText(
            f"当前：{Path(self.settings.asr.model).name or self.settings.asr.model}"
        )
        configured = self.settings.llm.configured
        llm_text = (
            f"已配置：{self.settings.llm.model}"
            if configured
            else "未配置 · 文稿精修与摘要将不可用"
        )
        self.home_llm_label.setText(llm_text)
        self.llm_summary.setText(
            
                f"接口：{self.settings.llm.api_base}\n模型：{self.settings.llm.model}"
                if configured
                else "尚未配置大模型；可先用「仅识别」模式产出逐字稿。"
            
        )

    def _refresh_nav_icons(self) -> None:
        colors = theme.palette(self.theme_name)
        current = self.nav.currentRow()
        for index, (_key, _label, icon_name) in enumerate(_NAV_ITEMS):
            item = self.nav.item(index)
            if item is None:
                continue
            color = colors["accent"] if index == current else colors["text_muted"]
            item.setIcon(theme.icon(icon_name, color, size=18))

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
            self._apply_theme()
        except OSError:
            self._append_log("主题偏好保存失败（不影响本次使用）")
        self.refresh_history()
        self._sync_recent()

    def _set_theme_value(self, value: str) -> None:
        if not self._apply_and_save({"ui": {"theme": value}}, f"主题已设为 {value}"):
            return
        self.theme_name = theme.resolve_theme(value)
        self._apply_theme()
        self.refresh_history()
        self._sync_recent()

    # ------------------------------------------------------------------ #
    # 工作台 / 资料库辅助
    # ------------------------------------------------------------------ #
    def _sync_recent(self) -> None:
        if not hasattr(self, "recent_list"):
            return
        self.recent_list.clear()
        count = self.history.count()
        for index in range(min(3, count)):
            source = self.history.item(index)
            if source is None:
                continue
            clone = QListWidgetItem(source.text())
            clone.setData(Qt.UserRole, source.data(Qt.UserRole))
            clone.setIcon(source.icon())
            self.recent_list.addItem(clone)
        has_items = self.recent_list.count() > 0
        self.recent_list.setVisible(has_items)
        self.recent_empty.setVisible(not has_items)

    def _open_recent_item(self, item: QListWidgetItem) -> None:
        data = item.data(Qt.UserRole) or {}
        key = str(data.get("key") or "")
        if not key:
            return
        self.nav.setCurrentRow(PAGE_LIBRARY)
        for row in range(self.history.count()):
            candidate = self.history.item(row)
            if candidate is not None and str((candidate.data(Qt.UserRole) or {}).get("key")) == key:
                self.history.setCurrentItem(candidate)
                break

    def _filter_history(self) -> None:
        text = getattr(self, "history_search", None)
        query = text.text().strip().lower() if text is not None else ""
        mode = self.history_filter.currentData() if hasattr(self, "history_filter") else "all"
        visible = 0
        for row in range(self.history.count()):
            item = self.history.item(row)
            if item is None:
                continue
            meta = item.data(Qt.UserRole) or {}
            haystack = (
                f"{item.text()} {meta.get('url', '')} {meta.get('key', '')}"
            ).lower()
            hidden = bool(query) and query not in haystack
            if not hidden and mode != "all":
                state = str(meta.get("status") or "open")
                if (
                    (mode == "done" and state != "done")
                    or (mode == "failed" and state != "failed")
                    or (mode == "open" and state not in ("open", "paused"))
                ):
                    hidden = True
            item.setHidden(hidden)
            visible += int(not hidden)
        current = self.history.currentItem()
        if current is not None and current.isHidden():
            for row in range(self.history.count()):
                candidate = self.history.item(row)
                if candidate is not None and not candidate.isHidden():
                    self.history.setCurrentItem(candidate)
                    break
            else:
                self.history.setCurrentItem(None)
        if visible == 0:
            has_any = self.history.count() > 0
            self.history_empty.set_texts(
                "没有匹配的任务" if has_any else "资料库还是空的",
                "换个关键词或状态筛选" if has_any else "完成第一条视频后，产物与进度会出现在这里",
            )
        self.history_empty.setVisible(visible == 0)

    @staticmethod
    def _ensure_combo_value(combo: QComboBox, value: str) -> None:
        """配置里已有但下拉框没有的值（如 pdf_scale=1.2）原样保留，不被首项覆盖。"""
        if not value:
            return
        index = combo.findData(value)
        if index < 0:
            combo.addItem(f"自定义（{value}）", value)
            index = combo.count() - 1
        combo.setCurrentIndex(index)

    def _save_asr_settings(self) -> None:
        patch = {
            "asr": {
                "model": self.set_asr_model.text().strip() or "large-v3",
                "device": self.set_asr_device.currentText(),
                "hf_endpoint": self.set_hf_endpoint.text().strip(),
            }
        }
        if self._apply_and_save(patch, "识别设置已保存"):
            self._apply_theme()

    def _test_llm_connection(self) -> None:
        from ..config import LlmSettings
        from .llm_wizard import ConnectionTestWorker

        worker = getattr(self, "_llm_test_worker", None)
        if worker is not None and worker.isRunning():
            return
        config = self.settings.llm
        if not config.api_key:
            self.llm_test_label.setText("先配置 API Key")
            return
        self.llm_test_btn.setEnabled(False)
        self.llm_test_label.setText("测试中…")
        self._llm_test_worker = ConnectionTestWorker(LlmSettings(**config.model_dump()))
        self._llm_test_worker.done.connect(self._on_llm_test_done)
        self._llm_test_worker.start()

    def _on_llm_test_done(self, ok: bool, message: str, hint: str) -> None:
        self.llm_test_btn.setEnabled(True)
        prefix = "✓" if ok else "✗"
        self.llm_test_label.setText(f"{prefix} {message}" + (f" · {hint}" if hint else ""))

    def _save_export_settings(self) -> None:
        patch = {
            "export": {
                "pdf": self.set_pdf.isChecked(),
                "docx": self.set_docx.isChecked(),
                "epub": self.set_epub.isChecked(),
                "pdf_style": self.set_pdf_style.currentData(),
                "pdf_mode": self.set_pdf_mode.currentData(),
                "pdf_scale": self.set_pdf_scale.currentData(),
            }
        }
        self._apply_and_save(patch, "导出设置已保存")

    # ------------------------------------------------------------------ #
    # 失败处理
    # ------------------------------------------------------------------ #
    def _show_error(self, title: str, message: str, hint: str) -> None:
        self.error_card.setVisible(True)
        self.error_message.setText(message)
        self.error_hint.setText(hint or "")
        self.error_hint.setVisible(bool(hint))
        self.task_pill.set_status(title, "failed")
        self.fix_model_btn.setVisible(
            "features shape" in f"{message} {hint}".lower() or "mel" in f"{message} {hint}".lower()
        )
        self._append_log(f"{title}：{message}" + (f"\n{hint}" if hint else ""))

    def _retry_failed(self) -> None:
        source = self._last_source or self.url_edit.text().strip()
        if not source:
            return
        step = self._failed_step or None
        self._start_with(source, step, key=self.last_key)

    def _copy_error(self) -> None:
        text = f"{self.error_message.text()}\n{self.error_hint.text()}".strip()
        QApplication.clipboard().setText(text)
        self._append_log("错误信息已复制到剪贴板")

    def _copy_log(self) -> None:
        QApplication.clipboard().setText(self.log_view.toPlainText())
        self._append_log("日志已复制到剪贴板")

    def _repair_from_error(self) -> None:
        import re

        from ..asr import resolve_model_reference
        from ..model_store import repair_model, write_preprocessor_config

        try:
            target = Path(resolve_model_reference(self.settings.asr.model))
        except VidarError as exc:
            self._append_log(f"修复失败：{exc.message}")
            self.error_hint.setText(exc.message + (f"\n{exc.hint}" if exc.hint else ""))
            return
        try:
            try:
                changed = repair_model(self.settings.asr.model, target)
            except VidarError:
                match = re.search(r"\(1,\s*(\d+),\s*3000\)", self.error_message.text())
                if match is None:
                    self._append_log("修复失败：无法从报错中推断 mel 维度")
                    return
                write_preprocessor_config(target, int(match.group(1)))
                changed = True
        except OSError as exc:
            self._append_log(f"修复失败：{exc}")
            self.error_hint.setText(f"无法写入模型目录：{exc}")
            return
        self._append_log(f"已{'写入' if changed else '确认'} preprocessor_config.json：{target}")
        self._retry_failed()

    def _repair_selected_model(self) -> None:
        from ..model_store import default_model_dir, find_model_dir, repair_model

        alias = self._selected_model_alias()
        if not alias:
            QMessageBox.information(self, "提示", "先在表格里选一个模型")
            return
        target = find_model_dir(alias) or default_model_dir(alias)
        if not (target / "model.bin").exists():
            self.model_status.setText(f"未安装：{target}")
            return
        try:
            changed = repair_model(alias, target)
        except VidarError as exc:
            self.model_status.setText(f"✗ {exc.message}")
            return
        self.model_status.setText(f"✓ 已{'写入' if changed else '确认'} preprocessor_config.json")
        self._append_log(f"模型特征配置：{target}")

    # ------------------------------------------------------------------ #
    # 队列持久化（关闭软件后仍可继续）
    # ------------------------------------------------------------------ #
    def _queue_path(self) -> Path:
        return resolve_path(self.settings.paths.work_dir) / "_queue.json"

    def _persist_queue(self) -> None:
        try:
            path = self._queue_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = [
                {
                    "source": item["source"],
                    "status": item["status"],
                    "error": item.get("error", ""),
                    "note": item.get("note", ""),
                }
                for item in self.batch_items
            ]
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            tmp.replace(path)
        except OSError as exc:
            self._append_log(f"队列未能保存：{exc}")

    def _restore_queue(self) -> None:
        try:
            path = self._queue_path()
            if not path.exists():
                return
            data = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError) as exc:
            backup = path.with_suffix(".json.bak")
            try:
                if path.exists() and not backup.exists():
                    backup.write_bytes(path.read_bytes())
            except OSError:
                pass
            self._append_log(f"队列文件无法解析，已备份到 {backup.name}：{exc}")
            return
        if not isinstance(data, list) or self.batch_items:
            return
        for entry in data:
            if not isinstance(entry, dict) or not entry.get("source"):
                continue
            status = str(entry.get("status") or "pending")
            note = str(entry.get("note") or "")
            if status == "running":
                status = "pending"
                note = "上次中断，可重新开始"
            elif status not in ("pending", "done", "failed", "cancelled"):
                status = "pending"
            self.batch_items.append(
                {
                    "source": str(entry["source"]),
                    "status": status,
                    "error": str(entry.get("error") or ""),
                    "note": note,
                }
            )
        if self.batch_items:
            self._refresh_batch_table()
            self._append_log(f"已恢复队列：{len(self.batch_items)} 个任务")

    def _start(self) -> None:
        source = self.url_edit.text().strip()
        if not source:
            QMessageBox.warning(self, "提示", "请先粘贴 BV 号或视频链接")
            return
        if self._task_busy():
            self._clear_pending_key()
            QMessageBox.information(self, "提示", "已有任务在运行，请先等待或取消")
            return

        # 只有来源一致时才沿用历史任务目录；否则按新链接重新生成 key，
        # 避免“载入历史后改了 URL”把新视频写进旧任务目录。
        pending_key = self._pending_key if self._pending_source == source else None
        self._pending_key = None
        self._pending_source = None
        self.last_key = pending_key or make_key(source)
        self._render_steps({name: "等待" for name in STEP_ORDER})
        self.progress.setValue(0)
        self.status_label.setText("启动中…")
        self.log_view.clear()
        self._last_source = source
        self._failed_step = ""
        self.error_card.setVisible(False)
        self.task_title.setText(source)
        self.task_meta.setText("正在解析视频信息…")
        self.task_kicker.setText("刚刚开始 · " + datetime.now().strftime("%H:%M"))
        self.task_pill.set_status("运行中", "running")
        self.nav.setCurrentRow(PAGE_TASK)
        self._set_running(True)

        self.worker = PipelineWorker(
            self.settings, source, self.from_combo.currentData(), key=pending_key
        )
        self.worker.event.connect(self._on_event)
        self.worker.failed.connect(self._on_failed)
        self.worker.ok.connect(self._on_ok)
        self.worker.finished.connect(self._on_worker_finished)
        self.worker.start()

    def _clear_pending_key(self, _text: str = "") -> None:
        self._pending_key = None
        self._pending_source = None

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
            self.task_pill.set_status("运行中", "running")
            self.task_kicker.setText(f"正在执行 · {data.get('title', step)}")
        elif event == "step_done":
            self._set_step_status(step, "完成 ✓")
            self.progress.setValue(0)
        elif event == "step_skipped":
            self._set_step_status(step, "跳过（已完成）")
        elif event == "step_cancelled":
            self._set_step_status(step, "已取消")
        elif event == "step_failed":
            self._set_step_status(step, "失败 ✗")
            self._failed_step = step
            self.task_pill.set_status("失败", "failed")
            self._append_log(f"步骤 [{step}] 失败：{data.get('error', '')}")
        elif event == "paused":
            self.status_label.setText("已暂停（可「继续」；或「释放显存」后从历史页续跑）")
            self.task_pill.set_status("已暂停", "paused")
            self.task_kicker.setText("已暂停 · 可继续或释放显存")
        elif event == "resumed":
            self.status_label.setText("继续运行…")
        elif event == "step_paused":
            self._append_log(f"步骤 [{step}] 已保存检查点并暂停（可继续）")
        elif event == "run_paused":
            self.status_label.setText("已暂停并释放资源：历史页可一键「继续」")
            self.task_pill.set_status("已暂停", "paused")
            self.task_kicker.setText("已释放显存 · 从资料库可继续")
            self._append_log("运行已暂停：检查点已保存，继续时不会重跑已完成部分")
        elif event == "run_finished":
            self.status_label.setText("全部完成")
            self.task_pill.set_status("已完成", "done")
            self.task_kicker.setText("全部步骤完成 · 产物已生成")
            self._append_log("全部步骤完成。")
        elif event == "cancelled":
            self.status_label.setText("已取消")
            self.task_pill.set_status("已取消", "cancelled")
            self.task_kicker.setText("已取消 · 可从中断步骤续跑")
            self._append_log("任务已取消（可从中断步骤续跑）。")

    def _on_failed(self, message: str, hint: str) -> None:
        self.status_label.setText("失败")
        self._show_error("任务失败", message, hint)

    def _on_ok(self) -> None:
        self.status_label.setText("完成 ✓")
        self.task_pill.set_status("已完成", "done")
        self.task_kicker.setText("全部步骤完成 · 产物已生成")

    def _on_worker_finished(self) -> None:
        if self.sender() is not self.worker:  # 旧 worker 的迟到信号（已启动新任务）
            return
        self._set_running(False)
        self.refresh_history()
        self._sync_recent()

    def _append_log(self, text: str) -> None:
        self.log_view.appendPlainText(text)
        scrollbar = self.log_view.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    # ------------------------------------------------------------------ #
    # 步骤表格
    # ------------------------------------------------------------------ #
    def _render_steps(self, statuses: dict[str, str]) -> None:
        self.steps_table.reset(statuses)

    def _set_step_status(self, name: str, text: str) -> None:
        self.steps_table.set_status(name, text)
        if text.startswith("失败"):
            self._failed_step = name

    # ------------------------------------------------------------------ #
    # 历史任务
    # ------------------------------------------------------------------ #
    def refresh_history(self) -> None:
        self.history.clear()
        work_root = resolve_path(self.settings.paths.work_dir)
        if not work_root.exists():
            self._filter_history()
            return
        def _mtime(path: Path) -> float:
            try:
                return path.stat().st_mtime
            except OSError:
                return 0.0

        folders = sorted(
            (p for p in work_root.iterdir() if p.is_dir() and not p.name.startswith("_")),
            key=_mtime,
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
            steps = state.get("steps")
            if not isinstance(steps, dict):
                steps = {}
            done = sum(
                1
                for rec in steps.values()
                if isinstance(rec, dict) and rec.get("status") == "done"
            )
            records = [rec for rec in steps.values() if isinstance(rec, dict)]
            all_done = bool(records) and all(
                rec.get("status") == "done" for rec in records
            )
            failed = [
                name
                for name, rec in steps.items()
                if isinstance(rec, dict) and rec.get("status") == "failed"
            ]
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
                    if isinstance(rec, dict)
                    and rec.get("status") == "pending"
                    and "已暂停" in str(rec.get("error") or "")
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
            elif all_done:
                suffix = "全部完成"
                icon_name, icon_color = "circle-check", colors["success"]
            else:
                suffix = f"{done}/{len(STEP_ORDER)} 步"
                icon_name, icon_color = "play", colors["text_muted"]
            display = title or url or folder.name
            if failed:
                state = "failed"
            elif paused_step:
                state = "paused"
            elif all_done:
                state = "done"
            else:
                state = "open"
            item = QListWidgetItem(f"{suffix} · {display}")
            item.setData(
                Qt.UserRole,
                {
                    "key": folder.name,
                    "url": url or folder.name,
                    "suffix": suffix,
                    "status": state,
                },
            )
            item.setToolTip(f"{display}\n{url or '（未解析）'}\n{suffix}")
            item.setIcon(theme.icon(icon_name, icon_color, size=16))
            item.setSizeHint(QSize(0, 46))
            self.history.addItem(item)
        self._filter_history()

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
            self.history_title.setVisible(False)
            self.history_meta.setVisible(False)
            self.history_detail.setVisible(False)
            self.detail_empty.setVisible(True)
            return
        self.history_title.setVisible(True)
        self.history_meta.setVisible(True)
        self.history_detail.setVisible(True)
        self.detail_empty.setVisible(False)
        data = current.data(Qt.UserRole) or {}
        key = str(data.get("key") or "")
        suffix = str(data.get("suffix") or "")
        self.history_title.setText(current.text())
        self.history_meta.setText(f"{suffix}　·　{data.get('url') or key}")
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
            record = steps.get(name)
            if not isinstance(record, dict):
                record = {}
            status = str(record.get("status") or "")
            note = str(record.get("error") or "")
            label = labels.get(status, "—")
            if status == "pending" and note:
                label = f"待续（{note}）"
            elif status == "failed" and note:
                label = f"失败（{note[:40]}）"
            row = self.history_detail.rowCount()
            self.history_detail.insertRow(row)
            step_item = QTableWidgetItem(_STEP_TITLES.get(name, name))
            step_item.setData(Qt.UserRole, name)
            self.history_detail.setItem(row, 0, step_item)
            self.history_detail.setItem(row, 1, QTableWidgetItem(name))
            self.history_detail.setItem(row, 2, QTableWidgetItem(label))
        # 默认选中第一个未完成步骤
        for row in range(self.history_detail.rowCount()):
            item = self.history_detail.item(row, 0)
            if item is None:
                continue
            step_name = str(item.data(Qt.UserRole) or "")
            status = str((steps.get(step_name) or {}).get("status") or "")
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
        if self._task_busy():
            QMessageBox.information(self, "提示", "已有任务在运行")
            return
        state_path = resolve_path(self.settings.paths.work_dir) / key / "state.json"
        if not state_path.exists():
            QMessageBox.warning(
                self,
                "提示",
                "该任务的 state.json 缺失或不可读，无法从断点继续。\n"
                "请用「重跑此步」指定步骤，或在任务页从头开始。",
            )
            return
        from_step = self._first_unfinished_step(key)
        if from_step is None:
            QMessageBox.information(
                self, "提示", "该任务已全部完成；可用「从选中步骤重跑」强制重算某一步"
            )
            return
        self._append_log(f"继续任务 {key}：从 {from_step} 步骤续跑")
        self._start_with(source, from_step, key=key)

    def _history_rerun_from_selected(self) -> None:
        key, source = self._selected_history_source()
        if not key:
            QMessageBox.information(self, "提示", "先在列表里选一条任务")
            return
        if self._task_busy():
            QMessageBox.information(self, "提示", "已有任务在运行")
            return
        row = self.history_detail.currentRow()
        if row < 0:
            QMessageBox.information(self, "提示", "先在「步骤明细」里选一个步骤")
            return
        item = self.history_detail.item(row, 0)
        step = str(item.data(Qt.UserRole) or "") if item is not None else ""
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
        self._start_with(source, step, key=key)

    def _start_with(self, source: str, from_step: str | None, key: str | None = None) -> None:
        self.url_edit.setText(source)
        index = STEP_ORDER.index(from_step) + 1 if from_step in STEP_ORDER else 0
        self.from_combo.setCurrentIndex(index)
        self._pending_key = key
        self._pending_source = source
        self.nav.setCurrentRow(PAGE_TASK)
        self._start()

    def _load_history_item(self, item: QListWidgetItem) -> None:
        data = item.data(Qt.UserRole) or {}
        loaded_source = str(data.get("url") or data.get("key") or "")
        self.url_edit.setText(loaded_source)
        self._pending_key = str(data.get("key") or "") or None
        self._pending_source = loaded_source
        from_step = self._first_unfinished_step(str(data.get("key") or ""))
        if from_step:
            index = STEP_ORDER.index(from_step) + 1  # 0 = 从头开始
            self.from_combo.setCurrentIndex(index)
            self._append_log(f"已载入任务 {data.get('key')}：将从 {from_step} 续跑")
        else:
            self.from_combo.setCurrentIndex(0)
            self._append_log(f"已载入任务 {data.get('key')}（已完成，重跑将全部跳过）")
        self.nav.setCurrentRow(PAGE_HOME)

    def _first_unfinished_step(self, key: str) -> str | None:
        state_path = resolve_path(self.settings.paths.work_dir) / key / "state.json"
        if not state_path.exists():
            return None
        try:
            steps = json.loads(state_path.read_text(encoding="utf-8")).get("steps")
        except (ValueError, OSError):
            return None
        if not isinstance(steps, dict):
            return None
        records = [rec for rec in steps.values() if isinstance(rec, dict)]
        if records and all(rec.get("status") == "done" for rec in records):
            return None  # 旧版步数更少但已全部完成：不要为了新步骤重跑旧产物
        for name in STEP_ORDER:
            record = steps.get(name)
            if not isinstance(record, dict) or record.get("status") != "done":
                return name
        return None

    # ------------------------------------------------------------------ #
    # 批量队列
    # ------------------------------------------------------------------ #
    def _task_busy(self) -> bool:
        task_running = self.worker is not None and self.worker.isRunning()
        batch_running = self.batch_worker is not None and self.batch_worker.isRunning()
        model_running = self.model_worker is not None and self.model_worker.isRunning()
        return task_running or batch_running or model_running

    def _append_batch_log(self, text: str) -> None:
        self.batch_log_view.appendPlainText(text)
        scrollbar = self.batch_log_view.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def _refresh_batch_table(self, *, persist: bool = True) -> None:
        labels = {
            "pending": "等待",
            "running": "运行中…",
            "done": "完成 ✓",
            "failed": "失败 ✗",
            "cancelled": "已停止",
        }
        self.batch_table.setRowCount(0)
        for index, item in enumerate(self.batch_items, start=1):
            row = self.batch_table.rowCount()
            self.batch_table.insertRow(row)
            self.batch_table.setItem(row, 0, QTableWidgetItem(str(index)))
            self.batch_table.setItem(row, 1, QTableWidgetItem(str(item["source"])))
            status = str(item["status"])
            status_item = QTableWidgetItem(labels.get(status, "—"))
            tone = {
                "done": "success",
                "failed": "danger",
                "running": "accent",
                "cancelled": "warning",
            }.get(status, "muted")
            colors = theme.palette(self.theme_name)
            status_item.setForeground(
                QColor(colors["text_muted"] if tone == "muted" else colors[tone])
            )
            self.batch_table.setItem(row, 2, status_item)
            note = str(item.get("note") or "")
            if item["status"] == "failed" and item.get("error"):
                note = str(item["error"])
            cell = QTableWidgetItem(note)
            cell.setToolTip(note)
            self.batch_table.setItem(row, 3, cell)
        if not self.batch_items:
            self.batch_status.setText("队列为空：粘贴多条链接后「加入队列」")
        elif self.batch_worker is None or not self.batch_worker.isRunning():
            pending = sum(1 for item in self.batch_items if item["status"] == "pending")
            done = sum(1 for item in self.batch_items if item["status"] == "done")
            failed = sum(1 for item in self.batch_items if item["status"] == "failed")
            parts = [f"共 {len(self.batch_items)} 条", f"待处理 {pending}"]
            if done:
                parts.append(f"完成 {done}")
            if failed:
                parts.append(f"失败 {failed}")
            self.batch_status.setText(" · ".join(parts) + "（点「开始队列」）")
        terminal = sum(
            1
            for item in self.batch_items
            if item["status"] in ("done", "failed", "cancelled")
        )
        self.batch_progress.setValue(
            int(1000 * terminal / len(self.batch_items)) if self.batch_items else 0
        )
        self.batch_count_label.setText(f"{len(self.batch_items)} 个任务")
        has_items = bool(self.batch_items)
        self.batch_table.setVisible(has_items)
        self.queue_empty.setVisible(not has_items)
        if persist:
            self._persist_queue()

    def _batch_add_sources(self, sources: list[str]) -> int:
        existing = {item["source"] for item in self.batch_items}
        added = 0
        for source in sources:
            text = source.strip()
            if not text or text.startswith("#") or text in existing:
                continue
            existing.add(text)
            self.batch_items.append({"source": text, "status": "pending", "error": "", "note": ""})
            added += 1
        return added

    def _batch_add(self) -> None:
        raw = self.batch_input.toPlainText()
        added = self._batch_add_sources([line.strip() for line in raw.splitlines()])
        if added:
            self.batch_input.clear()
            self._append_batch_log(f"已加入 {added} 条（去重后）。点「开始队列」处理。")
        else:
            self._append_batch_log("没有新增链接（空行 / 注释 / 重复项会被忽略）。")
        self._refresh_batch_table()

    def _batch_import(self) -> None:
        path, _filter = QFileDialog.getOpenFileName(
            self, "选择链接清单", "", "文本文件 (*.txt);;所有文件 (*)"
        )
        if not path:
            return
        from ..batch import read_sources

        try:
            sources = read_sources(Path(path))
        except VidarError as exc:
            QMessageBox.warning(
                self, "导入失败", exc.message + (f"\n{exc.hint}" if exc.hint else "")
            )
            return
        added = self._batch_add_sources(sources)
        self._append_batch_log(f"从文件导入 {added} 条（共 {len(sources)} 行有效链接）。")
        self._refresh_batch_table()

    def _batch_clear(self) -> None:
        if self._task_busy():
            QMessageBox.information(self, "提示", "有任务在运行，先等待完成或取消")
            return
        self.batch_items.clear()
        self._batch_done = 0
        self._batch_launched = 0
        self.batch_log_view.clear()
        self._append_batch_log("列表已清空。")
        self._refresh_batch_table()

    def _batch_remove_selected(self) -> None:
        if self._task_busy():
            QMessageBox.information(self, "提示", "有任务在运行，先等待完成或取消")
            return
        row = self.batch_table.currentRow()
        if row < 0 or row >= len(self.batch_items):
            QMessageBox.information(self, "提示", "先在列表里选一行")
            return
        removed = self.batch_items.pop(row)
        self._append_batch_log(f"已移除：{removed['source']}")
        self._refresh_batch_table()

    def _batch_retry_failed(self) -> None:
        if self._task_busy():
            QMessageBox.information(self, "提示", "有任务在运行，先等待完成或取消")
            return
        reset = 0
        for item in self.batch_items:
            if item["status"] in ("failed", "cancelled"):
                item["status"] = "pending"
                item["error"] = ""
                item["note"] = ""
                reset += 1
        self._refresh_batch_table()
        if not reset:
            self._append_batch_log("没有失败项需要重试。")
            return
        self._append_batch_log(f"已重置 {reset} 条失败/停止项，开始重跑。")
        self._batch_start()

    def _batch_start(self) -> None:
        if self._task_busy():
            QMessageBox.information(self, "提示", "已有任务在运行，请先等待或停止")
            return
        pending = [item for item in self.batch_items if item["status"] == "pending"]
        if not pending:
            QMessageBox.information(
                self, "提示", "队列里没有「等待」状态的链接：先加入或重试失败项"
            )
            return
        self._batch_running_sources = [str(item["source"]) for item in pending]
        self._batch_launched = len(pending)
        self._batch_done = 0
        self.batch_log_view.clear()
        self.batch_progress.setValue(0)
        self.batch_status.setText(f"共 {self._batch_launched} 条，开始处理…")
        self._set_batch_running(True)
        self._append_batch_log(f"队列开始：{self._batch_launched} 条")

        self.batch_worker = BatchWorker(self.settings, self._batch_running_sources)
        self.batch_worker.event.connect(self._on_batch_event)
        self.batch_worker.done.connect(self._on_batch_done)
        self.batch_worker.error.connect(self._on_batch_error)
        self.batch_worker.finished.connect(self._on_batch_finished)
        self.batch_worker.start()

    def _batch_stop(self) -> None:
        if self.batch_worker is None or not self.batch_worker.isRunning():
            return
        self.batch_stop_btn.setEnabled(False)
        self.batch_status.setText("已请求停止：当前条目安全退出后队列结束…")
        self._append_batch_log("已请求停止…")
        self.batch_worker.cancel()

    def _set_batch_running(self, running: bool) -> None:
        self.batch_start_btn.setEnabled(not running)
        self.batch_stop_btn.setEnabled(running)
        for button in (
            self.batch_add_btn,
            self.batch_import_btn,
            self.batch_clear_btn,
            self.batch_retry_btn,
            self.batch_remove_btn,
        ):
            button.setEnabled(not running)

    def _batch_item_for(self, source: str) -> dict | None:
        for item in self.batch_items:
            if item["source"] == source and item["status"] in ("pending", "running"):
                return item
        return None

    def _on_batch_event(self, event: str, data: dict) -> None:
        if event == "batch_item":
            source = str(data.get("source") or "")
            status = str(data.get("status") or "")
            item = self._batch_item_for(source)
            if item is not None:
                item["status"] = status or item["status"]
                item["error"] = str(data.get("error") or "")
                item["note"] = ""
            index = int(data.get("index") or 0)
            total = int(data.get("total") or 0)
            if status == "running":
                self.batch_status.setText(f"第 {index}/{total} 条：{source}")
                self._append_batch_log(f"▶ [{index}/{total}] {source}")
            elif status == "done":
                self._batch_done += 1
                self._append_batch_log(f"✓ [{index}/{total}] {source} 完成")
            elif status == "failed":
                self._batch_done += 1
                self._append_batch_log(
                    f"✗ [{index}/{total}] {source} 失败：{data.get('error') or '未知错误'}"
                )
            elif status == "cancelled":
                self._batch_done += 1
                self._append_batch_log(f"■ [{index}/{total}] {source} 已停止")
            self._refresh_batch_table()
            return
        if event == "batch_finished":
            done = int(data.get("done") or 0)
            failed = int(data.get("failed") or 0)
            self._batch_done = sum(
                1
                for entry in self.batch_items
                if entry["status"] in ("done", "failed", "cancelled")
            )
            self._refresh_batch_table()
            self.batch_status.setText(
                f"队列结束：完成 {done} / 失败 {failed}（失败项可「重试失败项」）"
            )
            self._append_batch_log(f"—— 队列结束：完成 {done}，失败 {failed} ——")
            return
        # 以下为转发自当前条目的流水线事件：更新「说明」列
        batch_index = int(data.get("batch_index") or 0)
        if not (1 <= batch_index <= len(self._batch_running_sources)):
            return
        source = self._batch_running_sources[batch_index - 1]
        item = self._batch_item_for(source)
        if item is None:
            return
        step = str(data.get("step") or "")
        if event == "step_started":
            item["note"] = str(data.get("title") or step)
        elif event == "progress":
            message = str(data.get("message") or "")
            done = float(data.get("done") or 0)
            total = float(data.get("total") or 0)
            percent = f"（{100 * min(1.0, done / total):.0f}%）" if total > 0 else ""
            item["note"] = f"{message}{percent}" if message else f"{step}{percent}"
        elif event == "step_done":
            item["note"] = f"{step} 完成"
        elif event == "step_failed":
            item["note"] = f"{step} 失败"
        elif event == "run_finished":
            item["note"] = "全部步骤完成"
        self._refresh_batch_table(persist=False)

    def _on_batch_done(self, _result: object) -> None:
        if not self.batch_status.text().startswith("队列结束"):
            self.batch_status.setText("队列结束")
        self.refresh_history()

    def _on_batch_error(self, message: str, hint: str) -> None:
        text = message + (f"\n\n{hint}" if hint else "")
        self.batch_status.setText("队列失败")
        self._append_batch_log(f"队列失败：{text}")
        QMessageBox.critical(self, "队列失败", text)

    def _on_batch_finished(self) -> None:
        if self.sender() is not self.batch_worker:  # 旧 worker 的迟到信号
            return
        self._set_batch_running(False)
        self.refresh_history()
        self._sync_recent()

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

    def _current_key(self, *, prefer_history: bool = False) -> str:
        if prefer_history:
            key, _ = self._selected_history_source()
            if key:
                return key
        key = self.last_key
        if key:
            return key
        item = self.history.currentItem()
        if item is not None:
            return str((item.data(Qt.UserRole) or {}).get("key") or "")
        return ""

    def _current_source(self, *, prefer_history: bool = False) -> str:
        """预览/重渲染用的任务来源：历史选中（可选优先）→ 最近任务 → 输入框。"""
        if prefer_history:
            _, selected = self._selected_history_source()
            if selected:
                return selected
        if self.last_key:
            return self.last_key
        item = self.history.currentItem()
        if item is not None:
            data = item.data(Qt.UserRole) or {}
            source = str(data.get("url") or data.get("key") or "")
            if source:
                return source
        return self.url_edit.text().strip()

    def _open_preview(self, *, prefer_history: bool = False) -> None:
        if self._task_busy():
            QMessageBox.information(
                self, "提示", "有任务在运行：预览重渲染会与流水线冲突，请先等待完成或取消"
            )
            return
        source = self._current_source(prefer_history=prefer_history)
        if not source:
            QMessageBox.information(self, "提示", "先跑一次任务，或在历史列表选一条，再打开预览")
            return
        self._preview_dialog = PreviewDialog(self.settings, source, self)
        self._preview_dialog.exec()
        self._preview_dialog = None

    def _open_result(self, *, notes: bool, prefer_history: bool = False) -> None:
        key = self._current_key(prefer_history=prefer_history)
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
        self._apply_and_save(dialog.patch(), "配置已保存")

    def _open_llm_wizard(self) -> None:
        dialog = LlmWizardDialog(self.settings, self)
        if dialog.exec() == QDialog.Accepted:
            self._apply_and_save(dialog.patch(), "LLM 配置已保存（接入向导）")
        else:
            # 「稍后再说」/关闭：记录已完成，避免每次启动都弹（可在关于页手动重开）
            self._apply_and_save({"ui": {"llm_wizard_done": True}}, "已跳过 LLM 接入向导")

    def _apply_and_save(self, patch: dict, message: str) -> bool:
        """合并 patch → 校验 → 写回配置文件 → 生效（设置 / 接入向导共用）。"""
        data = deep_merge(export_settings_dict(self.settings), patch)
        new_settings = Settings.model_validate(data)
        new_settings.config_path = self.settings.config_path
        target = new_settings.config_path or default_config_path()
        try:
            write_config_dict(target, export_settings_dict(new_settings))
        except OSError as exc:
            QMessageBox.critical(self, "保存失败", f"{target}\n{exc}")
            return False
        self.settings = new_settings
        self._append_log(f"{message}：{target}")
        return True

    # ------------------------------------------------------------------ #
    # 模型页
    # ------------------------------------------------------------------ #
    def refresh_models(self) -> None:
        from ..asr import cuda_available
        from ..model_store import (
            catalog,
            check_model,
            detect_vram_gb,
            find_model_dir,
            models_root,
            recommend_model,
        )

        root = models_root()
        self.model_table.setRowCount(0)
        colors = theme.palette(self.theme_name)
        for spec in catalog():
            found = find_model_dir(spec.alias)
            installed = False
            note = spec.note
            if found is not None:
                results = check_model(found.name, found)
                broken = [item.name for item in results if not item.ok]
                installed = not broken
                if broken:
                    note = "不完整，缺/坏：" + "、".join(broken)
                else:
                    note = f"{spec.note}　·　{found}"
            status_text = "已安装 ✓" if installed else ("不完整" if found else "—")
            status_item = QTableWidgetItem(status_text)
            status_item.setForeground(
                QColor(
                    colors["success"] if installed else colors["text_faint"]
                )
            )
            row = self.model_table.rowCount()
            self.model_table.insertRow(row)
            self.model_table.setItem(row, 0, QTableWidgetItem(spec.alias))
            self.model_table.setItem(row, 1, QTableWidgetItem(f"≈{spec.approx_mb / 1024:.1f} GB"))
            self.model_table.setItem(row, 2, QTableWidgetItem(f"≥{spec.vram_gb:.0f} GB"))
            self.model_table.setItem(row, 3, status_item)
            self.model_table.setItem(row, 4, QTableWidgetItem(note))
        if self.model_table.currentRow() < 0:
            self.model_table.selectRow(0)

        vram = detect_vram_gb()
        spec, reason = recommend_model(vram, has_cuda=cuda_available())
        self.model_banner.setText(f"{reason}　·　模型目录：{root}")
        total = 0
        for folder in root.glob("*"):
            model_bin = folder / "model.bin"
            if model_bin.exists():
                total += model_bin.stat().st_size
        self.model_disk_label.setText(f"模型目录：{root}　·　已占用 {total / 1024**3:.1f} GB")
        if vram:
            self.env_hw.setText(f"{vram:.1f} GB · CUDA")
        else:
            self.env_hw.setText("CUDA" if cuda_available() else "CPU 模式 · 较慢")
        self.home_model_label.setText(
            f"当前：{Path(self.settings.asr.model).name or self.settings.asr.model}"
        )

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
        self.model_status.setText(f"✗ 校验未通过：{bad}（点「修复特征配置」补齐）")
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

    # ------------------------------------------------------------------ #
    # 退出收尾
    # ------------------------------------------------------------------ #
    def closeEvent(self, event) -> None:  # noqa: ANN001, N802 - Qt 命名
        """运行中关闭窗口：先确认并安全取消，避免 QThread 被强杀崩溃。"""
        workers = [
            worker
            for worker in (
                self.worker,
                self.batch_worker,
                self.model_worker,
                getattr(self, "_llm_test_worker", None),
            )
            if worker is not None and worker.isRunning()
        ]
        if workers:
            answer = QMessageBox.question(
                self,
                "退出确认",
                "还有任务在运行：退出会取消当前任务\n"
                "（已完成的步骤与检查点会保留，下次可继续）。\n\n确定退出吗？",
            )
            if answer != QMessageBox.Yes:
                event.ignore()
                return
            for worker in workers:
                worker.cancel()  # type: ignore[attr-defined]
            for worker in workers:
                if not worker.wait(8000):
                    QMessageBox.information(
                        self,
                        "提示",
                        "正在安全取消（检查点已保存）…请稍候再试一次关闭。",
                    )
                    event.ignore()
                    return
        event.accept()


def run_gui(settings: Settings) -> None:
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("VIDAR")
    app.setWindowIcon(theme.app_icon())
    with contextlib.suppress(OSError):  # 日志目录不可写不阻断启动
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
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("VIDAR")
    try:
        settings = load_settings(None)
    except VidarError as exc:
        # 无控制台环境下也能看到失败原因（config.toml 损坏等）
        QMessageBox.critical(None, "VIDAR 启动失败", f"{exc.message}\n\n{exc.hint or ''}".strip())
        raise SystemExit(2) from exc
    run_gui(settings)


if __name__ == "__main__":
    main()

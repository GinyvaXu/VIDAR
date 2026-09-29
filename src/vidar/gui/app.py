"""VIDAR 桌面 GUI 主窗口。

设计原则：极简单窗口，一条 URL → 开始 → 看进度/日志 → 打开结果。
所有耗时操作在 QThread 里执行，通过事件信号回传 UI；日志走 logging handler 流转。
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QThread, Signal
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
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
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

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
from .settings_dialog import SettingsDialog

_STEP_TITLES = {step.name: step.title for step in default_steps()}  # type: ignore[attr-defined]


def _open_path(path: Path) -> None:
    if os.name == "nt":
        os.startfile(str(path))  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


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
            PipelineRunner(ctx, default_steps()).run(from_step=self._from_step)  # type: ignore[arg-type]
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


class MainWindow(QMainWindow):
    def __init__(self, settings: Settings) -> None:
        super().__init__()
        self.settings = settings
        self.worker: PipelineWorker | None = None
        self.last_key: str | None = None

        self.setWindowTitle("VIDAR — B站视频知识提取")
        self.resize(1100, 720)

        self._emitter = _Emitter()
        self._emitter.line.connect(self._append_log)
        logging.getLogger("vidar").addHandler(_QtLogHandler(self._emitter))

        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)

        # ---- 顶部：输入 + 控制 ----
        top = QHBoxLayout()
        self.url_edit = QLineEdit()
        self.url_edit.setPlaceholderText("粘贴 BV 号或视频链接，例如 BV1xx411c7mD")
        self.url_edit.returnPressed.connect(self._start)
        self.from_combo = QComboBox()
        self.from_combo.addItem("从头开始", None)
        for name in STEP_ORDER:
            self.from_combo.addItem(f"从 {name} 重跑", name)
        self.start_btn = QPushButton("开始")
        self.cancel_btn = QPushButton("取消")
        self.cancel_btn.setEnabled(False)
        top.addWidget(self.url_edit, 1)
        top.addWidget(self.from_combo)
        top.addWidget(self.start_btn)
        top.addWidget(self.cancel_btn)
        layout.addLayout(top)

        # ---- 进度条 ----
        progress_row = QHBoxLayout()
        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setValue(0)
        self.status_label = QLabel("就绪")
        progress_row.addWidget(self.progress, 1)
        progress_row.addWidget(self.status_label)
        layout.addLayout(progress_row)

        # ---- 中部：左（步骤 + 历史）右（日志） ----
        splitter = QSplitter(Qt.Horizontal)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.addWidget(QLabel("步骤进度"))
        self.steps_table = QTableWidget(0, 3)
        self.steps_table.setHorizontalHeaderLabels(["步骤", "内容", "状态"])
        self.steps_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.steps_table.verticalHeader().setVisible(False)
        self.steps_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.steps_table.setSelectionMode(QTableWidget.NoSelection)
        left_layout.addWidget(self.steps_table)
        left_layout.addWidget(QLabel("历史任务（双击载入并定位到未完成步骤）"))
        self.history = QListWidget()
        self.history.itemDoubleClicked.connect(self._load_history_item)
        left_layout.addWidget(self.history)
        splitter.addWidget(left)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.addWidget(QLabel("运行日志"))
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(5000)
        right_layout.addWidget(self.log_view)
        splitter.addWidget(right)
        splitter.setSizes([440, 640])
        layout.addWidget(splitter, 1)

        # ---- 底部按钮 ----
        bottom = QHBoxLayout()
        self.open_out_btn = QPushButton("打开输出目录")
        self.open_notes_btn = QPushButton("打开文稿")
        self.settings_btn = QPushButton("设置")
        self.refresh_btn = QPushButton("刷新历史")
        bottom.addWidget(self.open_out_btn)
        bottom.addWidget(self.open_notes_btn)
        bottom.addStretch(1)
        bottom.addWidget(self.refresh_btn)
        bottom.addWidget(self.settings_btn)
        layout.addLayout(bottom)

        # ---- 信号 ----
        self.start_btn.clicked.connect(self._start)
        self.cancel_btn.clicked.connect(self._cancel)
        self.settings_btn.clicked.connect(self._open_settings)
        self.refresh_btn.clicked.connect(self.refresh_history)
        self.open_out_btn.clicked.connect(lambda: self._open_result(notes=False))
        self.open_notes_btn.clicked.connect(lambda: self._open_result(notes=True))

        self._render_steps({})
        self.refresh_history()
        self._append_log("就绪：粘贴链接 → 开始。首次跑某条视频会自动解析并下载音频。")

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
            status_item = QTableWidgetItem(statuses.get(name, "等待"))
            self.steps_table.setItem(row, 2, status_item)

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
            (p for p in work_root.iterdir() if p.is_dir()),
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
            if failed:
                suffix = f"失败于 {failed[0]}"
            elif done >= len(STEP_ORDER):
                suffix = "全部完成"
            else:
                suffix = f"{done}/{len(STEP_ORDER)}"
            item = QListWidgetItem(f"{folder.name} · {title or '（未解析）'} · {suffix}")
            item.setData(Qt.UserRole, {"key": folder.name, "url": url or folder.name})
            self.history.addItem(item)

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

    def _open_result(self, *, notes: bool) -> None:
        key = self.last_key
        if not key:
            item = self.history.currentItem()
            if item is not None:
                key = str((item.data(Qt.UserRole) or {}).get("key") or "")
        if not key:
            QMessageBox.information(self, "提示", "还没有可打开的结果，先跑一次任务")
            return
        base = self._output_dir_for(key)
        if base is None:
            QMessageBox.information(self, "提示", "输出目录还不存在（任务可能还没跑到导出步骤）")
            return
        target = base
        if notes:
            for candidate_name in ("notes.md", "transcript.raw.md"):
                candidate = base / candidate_name
                if candidate.exists():
                    target = candidate
                    break
        _open_path(target)

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


def run_gui(settings: Settings) -> None:
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("VIDAR")
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

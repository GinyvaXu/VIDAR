"""GUI 设计系统与自绘控件的离线测试（不启动主窗口）。"""

from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from vidar.gui import theme  # noqa: E402
from vidar.gui.widgets import StatusPill, StepTimeline  # noqa: E402


@pytest.fixture(scope="module")
def qt_app():  # noqa: ANN201
    return QApplication.instance() or QApplication([])


def test_palettes_have_semantic_roles() -> None:
    for name in ("light", "dark"):
        colors = theme.palette(name)
        for key in ("bg", "panel", "text", "text_muted", "accent", "success", "danger", "warning"):
            assert colors[key].startswith("#")
    assert theme.palette("light")["accent"] == "#6C5CE7"


def test_status_tone_mapping() -> None:
    assert theme.status_tone("完成 ✓") == "success"
    assert theme.status_tone("失败 ✗") == "danger"
    assert theme.status_tone("运行中 62%") == "accent"
    assert theme.status_tone("已暂停") == "warning"
    assert theme.status_tone("等待") == "muted"


def test_qss_contains_theme_tokens() -> None:
    light = theme.build_qss("light")
    dark = theme.build_qss("dark")
    assert "#6C5CE7" in light and "#8F7BFF" in dark
    assert "#Card" in light and "QLineEdit" in light
    assert "QPushButton:focus" in light


def test_step_timeline_states(qt_app) -> None:  # noqa: ANN001
    timeline = StepTimeline([("resolve", "解析视频"), ("asr", "语音识别")])
    assert timeline.count() == 2
    timeline.set_status("resolve", "完成")
    timeline.set_status("asr", "失败")
    assert timeline.rows[0].state == "done"
    assert timeline.rows[1].state == "failed"
    timeline.reset({"asr": "运行中 62%"})
    assert timeline.rows[0].state == "pending"
    assert timeline.rows[1].state == "running"


def test_status_pill_state(qt_app) -> None:  # noqa: ANN001
    pill = StatusPill("等待")
    pill.set_status("识别中 62%", "running")
    assert "62%" in pill.text()
    assert pill._state == "running"

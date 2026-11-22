"""Clear Glass 的自绘控件：步骤时间线、状态徽章、空状态、文件行。

这些控件只负责“怎么画”，业务状态仍由 MainWindow 驱动（set_status / reset）。
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from . import theme

_STATE_TONE = {
    "done": "success",
    "running": "accent",
    "failed": "danger",
    "paused": "warning",
    "skipped": "warning",
    "pending": "muted",
    "cancelled": "muted",
}


def text_to_state(text: str) -> str:
    tone = theme.status_tone(text)
    return {
        "success": "done",
        "accent": "running",
        "danger": "failed",
        "warning": "paused",
        "muted": "pending",
    }.get(tone, "pending")


class StatusPill(QLabel):
    """状态徽章：图标点 + 文案 + 语义底色（不只靠颜色表达）。"""

    def __init__(self, text: str = "等待", state: str = "pending", parent=None) -> None:
        super().__init__(text, parent)
        self._state = state
        self.setObjectName("StatusPill")
        self.setAlignment(Qt.AlignCenter)
        self.setContentsMargins(10, 3, 10, 3)
        self._restyle()

    def set_status(self, text: str, state: str | None = None) -> None:
        self.setText(text)
        self._state = state or text_to_state(text)
        self._restyle()

    def set_state(self, state: str) -> None:
        self._state = state
        self._restyle()

    def _restyle(self) -> None:
        c = theme.palette(_current_theme())
        tone = _STATE_TONE.get(self._state, "muted")
        color = c.get(tone, c["text_muted"]) if tone != "muted" else c["text_muted"]
        soft = c.get(f"{tone}_soft", c["panel_alt"]) if tone != "muted" else c["panel_alt"]
        self.setStyleSheet(
            "QLabel#StatusPill {"
            f"background: {soft}; color: {color};"
            f"border-radius: 11px; font-size: 11px; font-weight: 600;"
            "}"
        )

    def apply_theme(self) -> None:
        self._restyle()

    def sizeHint(self):  # noqa: N802 - Qt 命名
        hint = super().sizeHint()
        hint.setHeight(max(22, hint.height()))
        return hint


def _current_theme() -> str:
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    return "dark" if app is not None and app.property("vidar_theme") == "dark" else "light"


class TimelineRow(QWidget):
    """单步：左侧圆圈 + 上下连接线，右侧标题/说明/状态。"""

    HEIGHT = 40

    def __init__(self, name: str, title: str, index: int, last: bool, parent=None) -> None:
        super().__init__(parent)
        self.name = name
        self.index = index
        self.last = last
        self.state = "pending"
        self.status_text = "等待"
        self.setFixedHeight(self.HEIGHT)

        self.title_label = QLabel(title)
        self.title_label.setStyleSheet("font-size: 12.5px; font-weight: 500;")
        self.meta_label = QLabel("等待")
        self.meta_label.setStyleSheet("font-size: 11px;")
        self.right_label = QLabel("")
        self.right_label.setStyleSheet("font-size: 10px;")
        self.right_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        text_box = QVBoxLayout()
        text_box.setContentsMargins(0, 0, 0, 0)
        text_box.setSpacing(1)
        text_box.addWidget(self.title_label)
        text_box.addWidget(self.meta_label)

        row = QHBoxLayout(self)
        row.setContentsMargins(34, 4, 8, 3)
        row.setSpacing(8)
        row.addLayout(text_box, 1)
        row.addWidget(self.right_label)

    def set_status(self, text: str, state: str | None = None) -> None:
        self.status_text = text
        self.state = state or text_to_state(text)
        self.meta_label.setText(text)
        self.right_label.setText("" if self.state == "running" else "")
        self.apply_theme()

    def apply_theme(self) -> None:
        c = theme.palette(_current_theme())
        tone = _STATE_TONE.get(self.state, "muted")
        color = c.get(tone, c["text_muted"]) if tone != "muted" else c["text_muted"]
        if self.state == "pending":
            self.title_label.setStyleSheet(
                f"font-size: 12.5px; font-weight: 500; color: {c['text_muted']};"
            )
            self.meta_label.setStyleSheet(f"font-size: 11px; color: {c['text_faint']};")
        elif self.state == "running":
            self.title_label.setStyleSheet(
                f"font-size: 12.5px; font-weight: 650; color: {c['text']};"
            )
            self.meta_label.setStyleSheet(f"font-size: 11px; color: {c['accent']};")
        else:
            self.title_label.setStyleSheet(
                f"font-size: 12.5px; font-weight: 500; color: {c['text']};"
            )
            self.meta_label.setStyleSheet(f"font-size: 11px; color: {color};")
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802, ANN001 - Qt 命名
        c = theme.palette(_current_theme())
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        # 连接线
        if not self.last:
            line_color = c["success"] if self.state == "done" else c["border"]
            painter.setPen(QPen(QColor(line_color), 1.2))
            painter.drawLine(10, 24, 10, self.height() + 2)

        # 圆圈
        rect = QRectF(1, 4, 18, 18)
        state = self.state
        if state == "done":
            fill, border, fg, text = c["success_soft"], c["success"], c["success"], "✓"
        elif state == "running":
            fill, border, fg, text = c["accent"], c["accent"], "#FFFFFF", str(self.index + 1)
        elif state == "failed":
            fill, border, fg, text = c["danger_soft"], c["danger"], c["danger"], "!"
        elif state == "paused":
            fill, border, fg, text = c["warning_soft"], c["warning"], c["warning"], "Ⅱ"
        else:
            fill, border, fg, text = (
                c["panel"],
                c["border_strong"],
                c["text_faint"],
                str(self.index + 1),
            )

        if state == "running":
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(c["accent_soft"]))
            painter.drawEllipse(QRectF(-2, 1, 24, 24))
        painter.setPen(QPen(QColor(border), 1.4))
        painter.setBrush(QColor(fill))
        painter.drawEllipse(rect)

        font = QFont(self.font())
        font.setPixelSize(9 if text != "Ⅱ" else 10)
        font.setBold(state in ("running", "done"))
        painter.setFont(font)
        painter.setPen(QColor(fg))
        painter.drawText(rect, Qt.AlignCenter, text)
        painter.end()

    def mousePressEvent(self, event) -> None:  # noqa: N802, ANN001
        super().mousePressEvent(event)


class StepTimeline(QWidget):
    """八步流水线的时间线；保持与旧 QTableWidget 相近的调用面。"""

    def __init__(self, steps: list[tuple[str, str]], parent=None) -> None:
        super().__init__(parent)
        self.rows: list[TimelineRow] = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(0)
        for index, (name, title) in enumerate(steps):
            row = TimelineRow(name, title, index, index == len(steps) - 1, self)
            self.rows.append(row)
            layout.addWidget(row)
        layout.addStretch(1)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

    def count(self) -> int:
        return len(self.rows)

    def reset(self, statuses: dict[str, str] | None = None) -> None:
        statuses = statuses or {}
        for row in self.rows:
            row.set_status(statuses.get(row.name, "等待"))
        self.apply_theme()

    def set_status(self, name: str, text: str, state: str | None = None) -> None:
        for row in self.rows:
            if row.name == name:
                row.set_status(text, state)
                return

    def apply_theme(self) -> None:
        for row in self.rows:
            row.apply_theme()


class EmptyState(QWidget):
    """空状态：一句人话 + 下一步动作（不给假内容）。"""

    def __init__(self, icon_name: str, title: str, subtitle: str, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(6)
        layout.addStretch(1)
        self.icon_name = icon_name
        self.icon_label = QLabel()
        self.icon_label.setAlignment(Qt.AlignCenter)
        self.apply_theme()
        self.head = QLabel(title)
        self.head.setAlignment(Qt.AlignCenter)
        self.head.setStyleSheet("font-size: 12.5px; font-weight: 600;")
        self.sub = QLabel(subtitle)
        self.sub.setAlignment(Qt.AlignCenter)
        self.sub.setWordWrap(True)
        self.sub.setObjectName("Muted")
        self.sub.setStyleSheet("font-size: 11px;")
        layout.addWidget(self.icon_label)
        layout.addWidget(self.head)
        layout.addWidget(self.sub)
        layout.addStretch(1)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

    def set_texts(self, title: str, subtitle: str) -> None:
        self.head.setText(title)
        self.sub.setText(subtitle)

    def apply_theme(self) -> None:
        color = theme.palette(_current_theme())["text_faint"]
        self.icon_label.setPixmap(theme.icon(self.icon_name, color, size=26).pixmap(26, 26))


class Card(QFrame):
    """带可选标题栏的卡片。"""

    def __init__(self, title: str = "", hint: str = "", parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("Card")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 15, 18, 16)
        outer.setSpacing(10)
        self.body = QVBoxLayout()
        self.body.setContentsMargins(0, 0, 0, 0)
        self.body.setSpacing(8)
        if title:
            head = QHBoxLayout()
            head.setContentsMargins(0, 0, 0, 0)
            title_label = QLabel(title)
            title_label.setObjectName("CardTitle")
            head.addWidget(title_label)
            head.addStretch(1)
            if hint:
                hint_label = QLabel(hint)
                hint_label.setObjectName("CardHint")
                head.addWidget(hint_label)
            self.header = head
            outer.addLayout(head)
        else:
            self.header = None
        outer.addLayout(self.body, 1)

    def add_header_widget(self, widget: QWidget) -> None:
        if self.header is not None:
            self.header.addWidget(widget)

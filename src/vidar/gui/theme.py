"""视觉设计系统：设计 token（明/暗双主题）+ QSS + 图标重着色。

- 所有颜色/字号/圆角/间距集中在 PALETTES，界面各处只引用 token；
- `build_qss(theme)` 生成全局样式表（QApplication.setStyleSheet 生效，含对话框）；
- `icon(name, color)` 读取 assets/icons 下的 currentColor SVG，按主题色渲染为 QIcon（带缓存）。
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from PySide6.QtCore import QByteArray, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

ICON_DIR = Path(__file__).resolve().parent.parent / "assets" / "icons"
APP_DIR = Path(__file__).resolve().parent.parent / "assets" / "app"

# --------------------------------------------------------------------------- #
# 设计 token
# --------------------------------------------------------------------------- #
PALETTES: dict[str, dict[str, str]] = {
    "light": {
        "bg": "#F4F5F9",
        "panel": "#FFFFFF",
        "panel_alt": "#FAFBFD",
        "border": "#E3E6EE",
        "border_strong": "#CBD1DE",
        "text": "#1B1F2A",
        "text_muted": "#6B7280",
        "accent": "#4F6BFF",
        "accent_hover": "#3F59E8",
        "accent_soft": "#EDF0FF",
        "success": "#16A34A",
        "danger": "#DC2626",
        "warning": "#D97706",
        "log_bg": "#0F1117",
        "log_text": "#D6DAE3",
        "scroll": "#C9CEDB",
    },
    "dark": {
        "bg": "#0F1115",
        "panel": "#171A21",
        "panel_alt": "#1C2029",
        "border": "#262B36",
        "border_strong": "#3A4150",
        "text": "#E8EAF0",
        "text_muted": "#9AA3B2",
        "accent": "#6B84FF",
        "accent_hover": "#7E93FF",
        "accent_soft": "#1E2540",
        "success": "#4ADE80",
        "danger": "#F87171",
        "warning": "#FBBF24",
        "log_bg": "#0B0D11",
        "log_text": "#C9D1DC",
        "scroll": "#333A47",
    },
}

RADIUS = {"sm": 6, "md": 10, "lg": 14}
SPACE = {"xs": 4, "sm": 8, "md": 12, "lg": 16, "xl": 24}
FONT_FAMILY = '"Microsoft YaHei UI", "Segoe UI", "PingFang SC", sans-serif'
FONT_MONO = '"Cascadia Mono", "Consolas", monospace'


def palette(theme: str) -> dict[str, str]:
    return PALETTES.get(theme, PALETTES["light"])


def resolve_theme(value: str) -> str:
    """auto → 跟随系统（Windows 注册表 AppsUseLightTheme）。"""
    value = (value or "auto").strip().lower()
    if value in ("light", "dark"):
        return value
    return system_theme()


def system_theme() -> str:
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
        ) as key:
            light, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
        return "light" if int(light) else "dark"
    except Exception:  # noqa: BLE001 - 非 Windows / 读取失败时用浅色
        return "light"


# --------------------------------------------------------------------------- #
# 图标（SVG → 主题色 → QIcon）
# --------------------------------------------------------------------------- #
@lru_cache(maxsize=256)
def _render_icon(name: str, color: str, size: int) -> QPixmap:
    svg_path = ICON_DIR / f"{name}.svg"
    source = svg_path.read_text(encoding="utf-8")
    source = source.replace("currentColor", color)
    renderer = QSvgRenderer(QByteArray(source.encode("utf-8")))
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    renderer.render(painter)
    painter.end()
    return pixmap


def icon(name: str, color: str, *, size: int = 20) -> QIcon:
    """主题色图标（缓存；动画 SVG 为静态首帧，Qt 不支持 SMIL 动画）。"""
    pixmap = _render_icon(name, color, size)
    return QIcon(pixmap)


def app_icon() -> QIcon:
    """应用图标（assets/app/vidar.ico；缺失时回退内置 sparkles）。"""
    ico = APP_DIR / "vidar.ico"
    if ico.exists():
        return QIcon(str(ico))
    return icon("sparkles", "#4F6BFF", size=64)


# --------------------------------------------------------------------------- #
# 全局 QSS
# --------------------------------------------------------------------------- #
def build_qss(theme: str) -> str:
    c = palette(theme)
    return f"""
* {{ outline: none; }}
QWidget {{
    font-family: {FONT_FAMILY};
    font-size: 13px;
    color: {c["text"]};
}}
QMainWindow, #Root {{ background: {c["bg"]}; }}

/* ---- 侧边栏 ---- */
#Sidebar {{ background: {c["panel"]}; border-right: 1px solid {c["border"]}; }}
#LogoTitle {{ font-size: 20px; font-weight: 700; letter-spacing: 1px; }}
#LogoSub {{ color: {c["text_muted"]}; font-size: 11px; }}
#NavList {{
    background: transparent; border: none; padding: 6px;
}}
#NavList::item {{
    height: 40px; border-radius: {RADIUS["md"]}px; padding-left: 10px; color: {c["text_muted"]};
}}
#NavList::item:hover {{ background: {c["accent_soft"]}; color: {c["text"]}; }}
#NavList::item:selected {{
    background: {c["accent_soft"]}; color: {c["accent"]}; font-weight: 600;
}}
#VersionLabel {{ color: {c["text_muted"]}; font-size: 11px; }}

/* ---- 卡片 ---- */
#Card {{
    background: {c["panel"]}; border: 1px solid {c["border"]};
    border-radius: {RADIUS["lg"]}px;
}}
#CardTitle {{ font-size: 14px; font-weight: 600; }}
#PageTitle {{ font-size: 22px; font-weight: 700; }}
#PageSub {{ color: {c["text_muted"]}; }}
#Muted {{ color: {c["text_muted"]}; }}
#Banner {{
    background: {c["accent_soft"]}; border: 1px solid {c["border"]};
    border-radius: {RADIUS["md"]}px; color: {c["text"]};
}}

/* ---- 按钮 ---- */
QPushButton {{
    background: {c["panel_alt"]}; border: 1px solid {c["border_strong"]};
    border-radius: {RADIUS["sm"]}px; padding: 6px 14px; color: {c["text"]};
}}
QPushButton:hover {{ border-color: {c["accent"]}; color: {c["accent"]}; }}
QPushButton:disabled {{ color: {c["text_muted"]}; border-color: {c["border"]}; }}
QPushButton#Primary {{
    background: {c["accent"]}; border: 1px solid {c["accent"]};
    color: #FFFFFF; font-weight: 600;
}}
QPushButton#Primary:hover {{
    background: {c["accent_hover"]}; border-color: {c["accent_hover"]}; color: #FFFFFF;
}}
QPushButton#Primary:disabled {{
    background: {c["border_strong"]}; border-color: {c["border_strong"]}; color: {c["panel"]};
}}
QPushButton#Ghost {{
    background: transparent; border: none; color: {c["text_muted"]}; padding: 6px 10px;
}}
QPushButton#Ghost:hover {{ color: {c["accent"]}; }}
QPushButton#Danger:hover {{ border-color: {c["danger"]}; color: {c["danger"]}; }}

/* ---- 输入 ---- */
QLineEdit, QComboBox, QPlainTextEdit, QTextEdit {{
    background: {c["panel_alt"]}; border: 1px solid {c["border_strong"]};
    border-radius: {RADIUS["sm"]}px; padding: 7px 10px; selection-background-color: {c["accent"]};
}}
QLineEdit:focus, QComboBox:focus, QPlainTextEdit:focus {{ border-color: {c["accent"]}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{
    background: {c["panel"]}; border: 1px solid {c["border"]};
    selection-background-color: {c["accent_soft"]}; selection-color: {c["accent"]};
}}

/* ---- 表格 / 列表 ---- */
QTableWidget, QListWidget {{
    background: {c["panel"]}; border: 1px solid {c["border"]};
    border-radius: {RADIUS["md"]}px; gridline-color: {c["border"]};
    alternate-background-color: {c["panel_alt"]};
}}
QHeaderView::section {{
    background: {c["panel_alt"]}; border: none; border-bottom: 1px solid {c["border"]};
    padding: 6px 8px; color: {c["text_muted"]}; font-weight: 600;
}}
QTableWidget::item:selected, QListWidget::item:selected {{
    background: {c["accent_soft"]}; color: {c["accent"]};
}}
QListWidget {{ padding: 4px; }}
QListWidget::item {{ padding: 7px 8px; border-radius: {RADIUS["sm"]}px; }}

/* ---- 进度条 ---- */
QProgressBar {{
    background: {c["panel_alt"]}; border: 1px solid {c["border"]};
    border-radius: {RADIUS["sm"]}px; text-align: center; height: 18px; color: {c["text_muted"]};
}}
QProgressBar::chunk {{ background: {c["accent"]}; border-radius: {RADIUS["sm"]}px; }}

/* ---- 日志 ---- */
#LogView {{
    background: {c["log_bg"]}; color: {c["log_text"]}; font-family: {FONT_MONO};
    border: 1px solid {c["border"]}; border-radius: {RADIUS["md"]}px; padding: 8px;
}}

/* ---- 滚动条 ---- */
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {c["scroll"]}; border-radius: 5px; min-height: 28px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {c["scroll"]}; border-radius: 5px; min-width: 28px; }}

/* ---- 其它 ---- */
QSplitter::handle {{ background: {c["border"]}; }}
QToolTip {{
    background: {c["panel"]}; color: {c["text"]}; border: 1px solid {c["border_strong"]};
    padding: 4px 8px; border-radius: {RADIUS["sm"]}px;
}}
QCheckBox {{ spacing: 8px; }}
QCheckBox::indicator {{
    width: 16px; height: 16px; border: 1px solid {c["border_strong"]};
    border-radius: 4px; background: {c["panel_alt"]};
}}
QCheckBox::indicator:checked {{ background: {c["accent"]}; border-color: {c["accent"]}; }}
QDialog {{ background: {c["bg"]}; }}
"""


__all__ = [
    "APP_DIR",
    "ICON_DIR",
    "PALETTES",
    "RADIUS",
    "SPACE",
    "app_icon",
    "build_qss",
    "icon",
    "palette",
    "resolve_theme",
    "system_theme",
]

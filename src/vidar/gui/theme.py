"""视觉设计系统 · Clear Glass（v0.15）。

设计语言：清晰的浅色分层 + 品牌紫 + 发丝线 + 唯一浮层阴影。
- 所有颜色 / 字号 / 圆角 / 间距集中在 PALETTES 与常量里，页面只引用 token。
- `build_qss(theme)` 生成全局样式（QApplication.setStyleSheet 生效，含对话框）。
- `icon(name, color)` 读取 assets/icons 下的 currentColor SVG，按主题色渲染。
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from PySide6.QtCore import QByteArray, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

ICON_DIR = Path(__file__).resolve().parent.parent / "assets" / "icons"
APP_DIR = Path(__file__).resolve().parent.parent / "assets" / "app"

# --------------------------------------------------------------------------- #
# 设计 token
# --------------------------------------------------------------------------- #
PALETTES: dict[str, dict[str, str]] = {
    "light": {
        "bg": "#F2F0F7",
        "panel": "#FFFFFF",
        "panel_alt": "#F8F7FC",
        "sidebar": "#F7F5FC",
        "border": "#E9E5F2",
        "border_strong": "#D8D3E6",
        "text": "#1D1834",
        "text_muted": "#7A7392",
        "text_faint": "#A49DB6",
        "accent": "#6C5CE7",
        "accent_hover": "#5B4BD0",
        "accent_soft": "#F1EEFF",
        "success": "#12855A",
        "success_soft": "#EAF7F1",
        "danger": "#C03A52",
        "danger_soft": "#FDEEF1",
        "warning": "#B7791F",
        "warning_soft": "#FBF3E2",
        "log_bg": "#FAF9FD",
        "log_text": "#4A4463",
        "scroll": "#D8D3E6",
        "hover": "#F4F1FC",
    },
    "dark": {
        "bg": "#121019",
        "panel": "#1A1724",
        "panel_alt": "#211D2E",
        "sidebar": "#161320",
        "border": "#2A2538",
        "border_strong": "#3B3450",
        "text": "#EDEAF7",
        "text_muted": "#A49DB6",
        "text_faint": "#6F6885",
        "accent": "#8F7BFF",
        "accent_hover": "#A28FFF",
        "accent_soft": "#26203F",
        "success": "#4ADE9B",
        "success_soft": "#173B2C",
        "danger": "#F87171",
        "danger_soft": "#3C1D24",
        "warning": "#FBBF24",
        "warning_soft": "#3B2F14",
        "log_bg": "#14111D",
        "log_text": "#C9C2DD",
        "scroll": "#3B3450",
        "hover": "#221E30",
    },
}

RADIUS = {"sm": 7, "md": 10, "lg": 16, "xl": 22}
SPACE = {"xs": 4, "sm": 8, "md": 12, "lg": 16, "xl": 24}
FONT_FAMILY = (
    '"Microsoft YaHei UI", "Segoe UI Variable Text", "Segoe UI", "PingFang SC", sans-serif'
)
FONT_MONO = '"Cascadia Mono", "Consolas", "Microsoft YaHei UI", monospace'

# 兼容旧引用
ACCENT = PALETTES["light"]["accent"]


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


def status_tone(status: str) -> str:
    """步骤/任务状态 → token 名称（success / danger / warning / accent / muted）。"""
    text = (status or "").strip().lower()
    if any(key in text for key in ("完成", "成功", "done", "ok")):
        return "success"
    if any(key in text for key in ("失败", "错误", "failed", "error")):
        return "danger"
    if any(key in text for key in ("暂停", "跳过", "警告", "paused", "skip")):
        return "warning"
    if any(key in text for key in ("运行", "进行", "running", "启动")):
        return "accent"
    return "muted"


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
    """主题色图标（缓存；动画 SVG 为静态首帧）。"""
    return QIcon(_render_icon(name, color, size))


def dot_pixmap(color: str, size: int = 8) -> QPixmap:
    """状态圆点（自绘，避免额外资源）。"""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor(color))
    painter.drawEllipse(0, 0, size, size)
    painter.end()
    return pixmap


def app_icon() -> QIcon:
    """应用图标（assets/app/vidar.ico；缺失时回退内置 sparkles）。"""
    ico = APP_DIR / "vidar.ico"
    if ico.exists():
        return QIcon(str(ico))
    return icon("sparkles", PALETTES["light"]["accent"], size=64)


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
QWidget#Page {{ background: transparent; }}

/* ---- 侧边栏 ---- */
#Sidebar {{
    background: {c["sidebar"]};
    border-right: 1px solid {c["border"]};
}}
#LogoTitle {{ font-size: 15px; font-weight: 700; letter-spacing: 3px; }}
#LogoSub {{ color: {c["text_muted"]}; font-size: 10px; }}
#NavList {{ background: transparent; border: none; padding: 2px; }}
#NavList::item {{
    height: 38px; border-radius: {RADIUS["md"]}px;
    padding-left: 11px; color: {c["text_muted"]};
}}
#NavList::item:hover {{ background: {c["hover"]}; color: {c["text"]}; }}
#NavList::item:selected {{
    background: {c["panel"]}; color: {c["accent"]}; font-weight: 600;
    border: 1px solid {c["border"]};
}}
#NavGroup {{ color: {c["text_faint"]}; font-size: 10px; letter-spacing: 2px; padding-left: 12px; }}
#EnvCard {{
    background: {c["panel"]};
    border: 1px solid {c["border"]};
    border-radius: {RADIUS["md"]}px;
}}
#EnvCard QLabel {{ font-size: 10px; color: {c["text_muted"]}; }}
#EnvCard QLabel#EnvStrong {{ color: {c["text"]}; font-size: 10.5px; font-weight: 600; }}
#VersionLabel {{ color: {c["text_faint"]}; font-size: 10px; }}

/* ---- 卡片与文本 ---- */
#Card {{
    background: {c["panel"]};
    border: 1px solid {c["border"]};
    border-radius: {RADIUS["lg"]}px;
}}
#CardTitle {{ font-size: 13px; font-weight: 600; letter-spacing: 0.2px; }}
#CardHint {{ color: {c["text_faint"]}; font-size: 10.5px; }}
#PageTitle {{ font-size: 24px; font-weight: 650; letter-spacing: -0.3px; }}
#HeroTitle {{ font-size: 23px; font-weight: 650; letter-spacing: -0.3px; }}
#PageSub {{ color: {c["text_muted"]}; font-size: 11.5px; }}
#Muted {{ color: {c["text_muted"]}; }}
#Faint {{ color: {c["text_faint"]}; font-size: 10.5px; }}
#Kicker {{ color: {c["text_faint"]}; font-size: 10px; letter-spacing: 1.5px; }}
#Banner {{
    background: {c["accent_soft"]}; border: 1px solid {c["border"]};
    border-radius: {RADIUS["md"]}px; color: {c["text"]};
}}
#Chip {{
    background: {c["panel_alt"]}; border: 1px solid {c["border"]};
    border-radius: 999px; padding: 4px 10px; color: {c["text_muted"]}; font-size: 10.5px;
}}
#ErrorCard {{
    background: {c["danger_soft"]};
    border: 1px solid {c["danger"]};
    border-radius: {RADIUS["md"]}px;
}}
#ErrorTitle {{ color: {c["danger"]}; font-size: 12px; font-weight: 650; }}
#ActionBar {{
    background: {c["panel"]};
    border: 1px solid {c["border"]};
    border-radius: {RADIUS["lg"]}px;
}}

/* ---- 按钮 ---- */
QPushButton {{
    background: {c["panel"]}; border: 1px solid {c["border_strong"]};
    border-radius: {RADIUS["sm"]}px; padding: 7px 14px; color: {c["text"]};
}}
QPushButton:hover {{ background: {c["hover"]}; border-color: {c["accent"]}; }}
QPushButton:pressed {{ background: {c["accent_soft"]}; }}
QPushButton:focus {{ border: 2px solid {c["accent"]}; padding: 6px 13px; }}
QPushButton:disabled {{
    color: {c["text_faint"]}; border-color: {c["border"]}; background: {c["panel_alt"]};
}}
QPushButton#Primary {{
    background: {c["accent"]}; border: 1px solid {c["accent"]};
    color: #FFFFFF; font-weight: 600; padding: 7px 16px;
}}
QPushButton#Primary:hover {{
    background: {c["accent_hover"]}; border-color: {c["accent_hover"]};
}}
QPushButton#Primary:disabled {{
    background: {c["border_strong"]}; border-color: {c["border_strong"]}; color: {c["panel"]};
}}
QPushButton#Soft {{
    background: {c["accent_soft"]}; border: 1px solid transparent;
    color: {c["accent"]}; font-weight: 600;
}}
QPushButton#Soft:hover {{ background: {c["accent_soft"]}; border-color: {c["accent"]}; }}
QPushButton#Ghost {{
    background: transparent; border: none; color: {c["text_muted"]}; padding: 6px 9px;
}}
QPushButton#Ghost:hover {{ background: {c["hover"]}; color: {c["text"]}; }}
QPushButton#Danger {{
    background: transparent; border: 1px solid transparent; color: {c["danger"]};
}}
QPushButton#Danger:hover {{ border-color: {c["danger"]}; background: {c["danger_soft"]}; }}
QPushButton#Danger:disabled {{ color: {c["text_faint"]}; border-color: transparent; }}
QPushButton#IconButton {{
    background: transparent; border: 1px solid {c["border"]};
    border-radius: {RADIUS["sm"]}px; padding: 5px;
}}
QPushButton#IconButton:hover {{ background: {c["hover"]}; }}

/* ---- 输入 ---- */
QLineEdit, QComboBox, QPlainTextEdit, QTextEdit, QSpinBox {{
    background: {c["panel_alt"]}; border: 1px solid {c["border"]};
    border-radius: {RADIUS["sm"]}px; padding: 8px 11px;
    selection-background-color: {c["accent"]}; selection-color: #FFFFFF;
}}
QLineEdit:hover, QComboBox:hover, QPlainTextEdit:hover {{ border-color: {c["border_strong"]}; }}
QLineEdit:focus, QComboBox:focus, QPlainTextEdit:focus, QTextEdit:focus {{
    border: 1px solid {c["accent"]}; background: {c["panel"]};
}}
QComboBox::drop-down {{ border: none; width: 24px; }}
QComboBox QAbstractItemView {{
    background: {c["panel"]}; border: 1px solid {c["border"]};
    selection-background-color: {c["accent_soft"]}; selection-color: {c["accent"]};
    padding: 4px;
}}
QPlainTextEdit#LogView {{
    background: {c["log_bg"]}; color: {c["log_text"]};
    font-family: {FONT_MONO}; font-size: 11px;
    border: 1px solid {c["border"]}; border-radius: {RADIUS["md"]}px; padding: 10px 12px;
}}

/* ---- 表格 / 列表 ---- */
QTableWidget, QListWidget {{
    background: transparent; border: none;
    gridline-color: {c["border"]};
    selection-background-color: {c["accent_soft"]};
    selection-color: {c["text"]};
}}
QTableWidget {{ alternate-background-color: {c["panel_alt"]}; }}
QHeaderView::section {{
    background: transparent; border: none;
    border-bottom: 1px solid {c["border"]};
    padding: 7px 8px; color: {c["text_faint"]}; font-size: 10.5px; font-weight: 600;
}}
QTableWidget::item {{
    padding: 7px 8px; border-bottom: 1px solid {c["border"]};
}}
QTableWidget::item:selected {{ background: {c["accent_soft"]}; color: {c["text"]}; }}
QTableWidget::item:focus {{ border: 1px solid {c["accent"]}; }}
QListWidget#HistoryList {{ padding: 2px; }}
QListWidget#HistoryList::item {{
    padding: 10px 11px; border-radius: {RADIUS["md"]}px;
    margin: 1px 2px; color: {c["text"]};
}}
QListWidget#HistoryList::item:hover {{ background: {c["hover"]}; }}
QListWidget#HistoryList::item:selected {{
    background: {c["panel"]}; border: 1px solid {c["border"]}; color: {c["text"]};
}}

/* ---- 进度条 ---- */
QProgressBar {{
    background: {c["panel_alt"]}; border: none;
    border-radius: 3px; height: 6px; text-align: center;
    color: {c["text_muted"]}; font-size: 10px;
}}
QProgressBar::chunk {{ background: {c["accent"]}; border-radius: 3px; }}
QProgressBar#Slim {{ height: 4px; }}

/* ---- 滚动条 ---- */
QScrollBar:vertical {{ background: transparent; width: 9px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {c["scroll"]}; border-radius: 4px; min-height: 28px; }}
QScrollBar::handle:vertical:hover {{ background: {c["border_strong"]}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QScrollBar:horizontal {{ background: transparent; height: 9px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {c["scroll"]}; border-radius: 4px; min-width: 28px; }}

/* ---- 其它 ---- */
QSplitter::handle {{ background: {c["border"]}; }}
QToolTip {{
    background: {c["panel"]}; color: {c["text"]}; border: 1px solid {c["border_strong"]};
    padding: 5px 9px; border-radius: {RADIUS["sm"]}px;
}}
QCheckBox {{ spacing: 8px; color: {c["text"]}; }}
QCheckBox::indicator {{
    width: 16px; height: 16px; border: 1px solid {c["border_strong"]};
    border-radius: 5px; background: {c["panel"]};
}}
QCheckBox::indicator:checked {{ background: {c["accent"]}; border-color: {c["accent"]}; }}
QCheckBox::indicator:focus {{ border: 2px solid {c["accent"]}; }}
QDialog {{ background: {c["bg"]}; }}
QTabWidget::pane {{
    border: 1px solid {c["border"]}; border-radius: {RADIUS["md"]}px; background: {c["panel"]};
}}
QTabBar::tab {{
    background: transparent; color: {c["text_muted"]};
    padding: 8px 14px; margin-right: 4px; border-radius: {RADIUS["sm"]}px;
}}
QTabBar::tab:selected {{ background: {c["accent_soft"]}; color: {c["accent"]}; font-weight: 600; }}
QScrollArea {{ border: none; background: transparent; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
"""


__all__ = [
    "ACCENT",
    "APP_DIR",
    "FONT_FAMILY",
    "FONT_MONO",
    "ICON_DIR",
    "PALETTES",
    "RADIUS",
    "SPACE",
    "app_icon",
    "build_qss",
    "dot_pixmap",
    "icon",
    "palette",
    "resolve_theme",
    "status_tone",
    "system_theme",
]

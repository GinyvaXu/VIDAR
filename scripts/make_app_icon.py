"""生成应用图标（vidar.ico + 多尺寸 PNG）：从 assets/app/icon.svg 渲染。

用法：uv run python scripts/make_app_icon.py
产物：src/vidar/assets/app/vidar.ico、vidar-{16,24,32,48,64,128,256}.png
"""

from __future__ import annotations

import contextlib
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image  # noqa: E402
from PySide6.QtCore import QByteArray, Qt  # noqa: E402
from PySide6.QtGui import QGuiApplication, QImage, QPainter  # noqa: E402
from PySide6.QtSvg import QSvgRenderer  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
APP_DIR = ROOT / "src" / "vidar" / "assets" / "app"
SIZES = (16, 24, 32, 48, 64, 128, 256)

with contextlib.suppress(Exception):  # Windows 控制台默认 GBK：强制 UTF-8 输出
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]


def render_png(svg_text: str, size: int, output: Path) -> None:
    renderer = QSvgRenderer(QByteArray(svg_text.encode("utf-8")))
    image = QImage(size, size, QImage.Format_ARGB32)
    image.fill(Qt.transparent)
    painter = QPainter(image)
    renderer.render(painter)
    painter.end()
    image.save(str(output))


def main() -> None:
    app = QGuiApplication.instance() or QGuiApplication(sys.argv)  # noqa: F841
    svg_text = (APP_DIR / "icon.svg").read_text(encoding="utf-8")
    for size in SIZES:
        render_png(svg_text, size, APP_DIR / f"vidar-{size}.png")
    base = Image.open(APP_DIR / "vidar-256.png").convert("RGBA")
    base.save(APP_DIR / "vidar.ico", sizes=[(size, size) for size in SIZES])
    print("✓ 已生成：", APP_DIR / "vidar.ico", "（多尺寸 PNG 一并输出）")


if __name__ == "__main__":
    main()

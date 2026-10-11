"""VIDAR：B 站视频 → Markdown 知识文档。

版本号唯一来源：项目根目录的 VERSION 文件（打包后回退到包元数据）。
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version
from pathlib import Path


def _read_version() -> str:
    candidates = [
        Path(__file__).resolve().parent.parent.parent / "VERSION",  # 源码仓库根
        Path(__file__).resolve().parent.parent / "VERSION",  # PyInstaller _internal/
        Path(__file__).resolve().parent / "VERSION",  # 打包进包内
    ]
    for candidate in candidates:
        try:
            if candidate.exists():
                # utf-8-sig：防御 Windows 编辑器写入的 BOM 混入版本字符串
                return candidate.read_text(encoding="utf-8-sig").strip()
        except OSError:
            continue
    try:
        return _pkg_version("vidar")
    except PackageNotFoundError:
        return "0.0.0"


__version__ = _read_version()

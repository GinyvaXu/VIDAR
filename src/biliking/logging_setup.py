"""日志：终端 Rich 输出 + 文件落盘（logs/biliking.log）。"""

from __future__ import annotations

import logging
from pathlib import Path

from rich.logging import RichHandler

_LOGGER_NAME = "biliking"


def get_logger(name: str = "") -> logging.Logger:
    return logging.getLogger(f"{_LOGGER_NAME}.{name}" if name else _LOGGER_NAME)


def setup_logging(logs_dir: Path, *, verbose: bool = False, quiet: bool = False) -> None:
    logs_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(_LOGGER_NAME)
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()
    logger.propagate = False

    file_handler = logging.FileHandler(logs_dir / "biliking.log", encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    )
    logger.addHandler(file_handler)

    console_level = logging.DEBUG if verbose else (logging.WARNING if quiet else logging.INFO)
    console = RichHandler(show_time=False, show_path=False, markup=False, rich_tracebacks=True)
    console.setLevel(console_level)
    logger.addHandler(console)

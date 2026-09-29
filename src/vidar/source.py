"""视频源解析工具（BV 号 / URL 归一化）。"""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

from .errors import VidarError
from .utils.text import parse_bvid


def normalize_source(source: str) -> str:
    """BV 号 → 完整 URL；校验 URL 合法性。"""
    source = source.strip()
    bvid = parse_bvid(source)
    if bvid and "http" not in source.lower():
        return f"https://www.bilibili.com/video/{bvid}"
    if not source.lower().startswith("http"):
        raise VidarError(
            f"无法识别的视频地址：{source}",
            hint="请输入形如 BV1xxxxxxxxx 的 BV 号，或完整视频 URL",
        )
    return source


def extract_page(url: str) -> int:
    """从 URL 中提取分 P 号（?p=2），默认 1。"""
    try:
        query = parse_qs(urlparse(url).query)
        return int(query.get("p", ["1"])[0])
    except (ValueError, TypeError):
        return 1

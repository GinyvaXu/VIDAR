"""视频源解析工具（BV 号 / URL 归一化）。"""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

from .errors import VidarError
from .utils.text import parse_bvid


def normalize_source(source: str) -> str:
    """BV 号 → 完整 URL；无 scheme 的站内地址补 https；校验合法性。"""
    source = source.strip()
    bvid = parse_bvid(source)
    lowered = source.lower()
    if bvid and "http" not in lowered and "/" not in source:
        # 纯 BV 号（无路径/查询参数）才走规范 URL；带 ?p=2 等参数的原样补 https
        return f"https://www.bilibili.com/video/{bvid}"
    if not lowered.startswith("http"):
        if "bilibili.com" in lowered or "b23.tv" in lowered:
            return "https://" + source
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

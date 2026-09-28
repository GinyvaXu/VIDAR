"""文本工具：时间格式化、断句、slug、分块。

这些函数不依赖任何业务模型（duck typing），方便单测与复用。
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from typing import Protocol

CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
BV_RE = re.compile(r"BV[0-9A-Za-z]{10}")

# 句末标点（含中文省略号）；后接引号/括号也视为句末
_SENT_BREAK = re.compile(r"(?<=[。！？!?；;…])|(?<=[。！？!?；;…][」』”’）)】])")

_ILLEGAL_FILENAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_WINDOWS_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


class HasText(Protocol):
    text: str


def is_cjk(char: str) -> bool:
    return bool(CJK_RE.match(char))


def cjk_ratio(text: str) -> float:
    if not text:
        return 0.0
    return sum(1 for ch in text if CJK_RE.match(ch)) / len(text)


def normalize_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def format_clock(seconds: float, *, pad: bool = False) -> str:
    """秒 → '12:34' / '1:02:03'（pad=True 时强制 HH:MM:SS）。"""
    total = max(0, int(round(seconds)))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        head = f"{hours:02d}" if pad else f"{hours}"
        return f"{head}:{minutes:02d}:{secs:02d}"
    if pad:
        return f"00:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def format_srt_time(seconds: float) -> str:
    """秒 → 'HH:MM:SS,mmm'（SRT 格式）。"""
    ms_total = max(0, int(round(seconds * 1000)))
    hours, rem = divmod(ms_total, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    secs, ms = divmod(rem, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{ms:03d}"


def split_sentences(text: str) -> list[str]:
    """中英文混合断句，保留标点。"""
    text = text.strip()
    if not text:
        return []
    return [part.strip() for part in _SENT_BREAK.split(text) if part.strip()]


def estimate_tokens(text: str) -> int:
    """粗略 token 估算：CJK 每字 ≈ 1 token，其余字符 ≈ 1/3 token。"""
    if not text:
        return 0
    cjk = sum(1 for ch in text if CJK_RE.match(ch))
    others = len(text) - cjk
    return max(1, cjk + others // 3)


def slugify(text: str, *, max_len: int = 40, fallback: str = "untitled") -> str:
    """文件名安全化：保留中文，剔除 Windows 非法字符，压缩空白，限长。"""
    cleaned = _ILLEGAL_FILENAME.sub("", text)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    if not cleaned:
        return fallback
    if len(cleaned) > max_len:
        cleaned = cleaned[:max_len].rstrip(" .")
    if cleaned.upper() in _WINDOWS_RESERVED:
        cleaned = f"_{cleaned}"
    return cleaned or fallback


def parse_bvid(text: str) -> str | None:
    """从 URL 或纯文本中提取 BV 号。"""
    match = BV_RE.search(text)
    return match.group(0) if match else None


def chunk_by_chars(
    items: Sequence[HasText],
    *,
    max_chars: int,
    get_text=lambda item: item.text,  # type: ignore[no-untyped-def]
) -> list[list[HasText]]:
    """按字符预算把连续条目打包成块（条目本身不切分）。"""
    chunks: list[list[HasText]] = []
    current: list[HasText] = []
    size = 0
    for item in items:
        text_len = len(get_text(item))
        if current and size + text_len > max_chars:
            chunks.append(current)
            current, size = [], 0
        current.append(item)
        size += text_len
    if current:
        chunks.append(current)
    return chunks


def item_texts(items: Iterable[HasText]) -> list[str]:
    return [item.text for item in items]

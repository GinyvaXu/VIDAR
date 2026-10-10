"""VIDAR 领域数据模型。

约定：所有时间均为「秒(float)」，格式化只在导出层进行；
每个步骤的产物都是这些模型的 JSON 序列化，落盘于 work/<key>/。
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


def _now() -> datetime:
    return datetime.now()


class StepStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    SKIPPED = "skipped"


# --------------------------------------------------------------------------- #
# resolve 产物
# --------------------------------------------------------------------------- #
class VideoMeta(BaseModel):
    bvid: str
    cid: int | None = None
    page: int = 1
    title: str
    up_name: str = ""
    up_mid: int | None = None
    url: str
    pubdate: datetime | None = None
    duration_sec: float | None = None
    description: str = ""
    thumbnail_url: str = ""
    tags: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# asr 产物
# --------------------------------------------------------------------------- #
class AsrWord(BaseModel):
    start: float
    end: float
    text: str
    probability: float | None = None


class AsrSegment(BaseModel):
    idx: int
    start: float
    end: float
    text: str
    words: list[AsrWord] = Field(default_factory=list)


class AsrResult(BaseModel):
    engine: str
    model: str
    language: str = "zh"
    duration_sec: float | None = None
    created_at: datetime = Field(default_factory=_now)
    segments: list[AsrSegment] = Field(default_factory=list)

    @property
    def full_text(self) -> str:
        return "".join(seg.text for seg in self.segments)


# --------------------------------------------------------------------------- #
# refine 产物
# --------------------------------------------------------------------------- #
class RefinedSegment(BaseModel):
    """段级精修：时间戳与原始段落完全一致，保证可追溯。"""

    idx: int
    start: float
    end: float
    raw_text: str
    refined_text: str


class RefineResult(BaseModel):
    model: str
    created_at: datetime = Field(default_factory=_now)
    tokens_in: int = 0
    tokens_out: int = 0
    segments: list[RefinedSegment] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# chapters / summarize 产物
# --------------------------------------------------------------------------- #
class FigureHint(BaseModel):
    """值得配图的时刻（由 LLM 判断，本地映射回时间戳）。"""

    start: float
    caption: str = ""
    anchor: int | None = None


class Chapter(BaseModel):
    index: int
    title: str
    start: float
    end: float | None = None
    summary: str = ""
    quote: str = ""
    figures: list[FigureHint] = Field(default_factory=list)


class KeyPoint(BaseModel):
    text: str
    start: float | None = None


class SummaryResult(BaseModel):
    model: str
    created_at: datetime = Field(default_factory=_now)
    one_liner: str = ""
    key_points: list[KeyPoint] = Field(default_factory=list)
    chapters: list[Chapter] = Field(default_factory=list)
    tokens_in: int = 0
    tokens_out: int = 0


# --------------------------------------------------------------------------- #
# translate 产物（双语对照）
# --------------------------------------------------------------------------- #
class TranslatedSegment(BaseModel):
    """逐段翻译：段号/时间戳与精修稿 1:1 对应。"""

    idx: int
    start: float
    end: float
    source_text: str  # 翻译时的源文本快照（= 精修稿该段）
    translated_text: str


class TranslateResult(BaseModel):
    model: str
    created_at: datetime = Field(default_factory=_now)
    source_language: str = ""
    target_language: str = "zh"
    tokens_in: int = 0
    tokens_out: int = 0
    segments: list[TranslatedSegment] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# paragraphs 产物（语段编辑）
# --------------------------------------------------------------------------- #
class ParagraphGroup(BaseModel):
    """一个自然段：由若干连续段号拼接而成（时间戳取首段）。"""

    index: int
    start: float
    end: float
    text: str  # 目标语言正文（拼接）
    source_text: str = ""  # 源语言原文（有译文时用于双语对照）
    segments: list[int] = Field(default_factory=list)


class ParagraphResult(BaseModel):
    model: str
    created_at: datetime = Field(default_factory=_now)
    tokens_in: int = 0
    tokens_out: int = 0
    paragraphs: list[ParagraphGroup] = Field(default_factory=list)

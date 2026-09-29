"""章节切分（LLM）：精修稿 → 章节目录（标题 + 起始时间）。

失败兜底：LLM 不可用/输出不合法时，按时长机械切分，保证导出不中断。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .llm.client import LlmClient
from .llm.prompts import CHAPTERS_SYSTEM, chapters_prompt
from .models import Chapter, RefineResult

if TYPE_CHECKING:
    from .pipeline.context import RunContext


def _expected_count(duration_sec: float) -> str:
    if duration_sec <= 600:
        return "3-6"
    if duration_sec <= 1800:
        return "4-8"
    return "5-12"


def _mechanical_chapters(refine: RefineResult) -> list[Chapter]:
    """按时长机械切分（约 5 分钟一章），作为兜底。"""
    segments = refine.segments
    if not segments:
        return []
    duration = segments[-1].end
    count = max(2, min(10, int(duration // 300) or 2))
    size = max(1, len(segments) // count)
    chapters: list[Chapter] = []
    for i in range(0, len(segments), size):
        group = segments[i : i + size]
        if not group:
            continue
        chapters.append(
            Chapter(
                index=len(chapters),
                title=f"第 {len(chapters) + 1} 部分",
                start=group[0].start,
                end=group[-1].end,
            )
        )
    return chapters


def build_chapters(ctx: RunContext, refine: RefineResult) -> tuple[list[Chapter], LlmClient]:
    client = LlmClient(ctx.settings.llm)
    model = ctx.settings.llm.model
    duration = refine.segments[-1].end if refine.segments else 0.0

    ctx.emit("progress", step="chapters", done=0, total=1, message="请求章节切分…")
    paragraphs = "\n".join(f"[{seg.idx}] {seg.refined_text}" for seg in refine.segments)
    try:
        payload = client.complete_json(
            chapters_prompt(paragraphs, expected=_expected_count(duration)),
            system=CHAPTERS_SYSTEM,
            model=model,
        )
    except Exception as exc:  # noqa: BLE001 - 兜底不阻断导出
        ctx.log.warning("章节切分失败（%s），改用机械切分", exc)
        return _mechanical_chapters(refine), client

    start_by_index = {seg.idx: seg.start for seg in refine.segments}
    end_by_index = {seg.idx: seg.end for seg in refine.segments}
    parsed: list[tuple[int, str]] = []
    for item in payload.get("chapters") or []:
        if not isinstance(item, dict):
            continue
        index = item.get("start_index")
        title = str(item.get("title") or "").strip()
        if isinstance(index, int) and index in start_by_index and title:
            parsed.append((index, title))
    if not parsed:
        ctx.log.warning("章节切分结果不可用，改用机械切分")
        return _mechanical_chapters(refine), client

    parsed.sort(key=lambda pair: pair[0])
    # 第一章强制从开头开始；去重相邻相同起点
    dedup: list[tuple[int, str]] = [parsed[0]]
    for index, title in parsed[1:]:
        if index != dedup[-1][0]:
            dedup.append((index, title))
    if dedup and dedup[0][0] != refine.segments[0].idx:
        dedup[0] = (refine.segments[0].idx, dedup[0][1])

    chapters: list[Chapter] = []
    for order, (index, title) in enumerate(dedup):
        start = start_by_index[index]
        end = end_by_index[index]
        if order + 1 < len(dedup):
            end = start_by_index.get(dedup[order + 1][0], end)
        else:
            end = refine.segments[-1].end if refine.segments else end
        chapters.append(Chapter(index=order, title=title, start=start, end=end))

    ctx.emit("progress", step="chapters", done=1, total=1, message=f"共 {len(chapters)} 章")
    return chapters, client

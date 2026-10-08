"""摘要提炼（map-reduce）：精修稿 → 一句话结论 + 核心要点 + 章节小结。

时间锚点全程本地映射（LLM 只输出段号），保证可追溯、杜绝时间戳幻觉。
reduce 丢失锚点时，用模糊匹配（difflib）从 map 要点里找回。
"""

from __future__ import annotations

import difflib
import json
import os
from typing import TYPE_CHECKING, Any

from .llm.client import LlmClient
from .llm.prompts import (
    MAP_SYSTEM,
    REDUCE_SYSTEM,
    map_summary_prompt,
    reduce_summary_prompt,
)
from .models import Chapter, FigureHint, KeyPoint, RefineResult, SummaryResult
from .utils.text import chunk_by_chars

if TYPE_CHECKING:
    from .pipeline.context import RunContext


_PLACEHOLDER_MARKERS = (
    "未提供",
    "没有提供",
    "无法确定",
    "未找到",
    "信息不足",
    "未展开",
    "未涉及",
    "给定要点",
)


def _clean_summary(text: str) -> str:
    """过滤"给定要点未提供…"这类占位话术。"""
    if any(marker in text for marker in _PLACEHOLDER_MARKERS):
        return ""
    return text


def _payload_has_figures(payload: dict) -> bool:
    """检查 reduce 返回里是否存在任何配图锚点。"""
    for item in payload.get("chapters") or payload.get("chapter_summaries") or []:
        if isinstance(item, dict) and item.get("figures"):
            return True
    return False


def _normalize_anchor(value: object) -> int | None:
    """容忍 LLM 返回字符串形式的段号。"""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _recover_anchor(text: str, points: list[dict[str, Any]]) -> int | None:
    """reduce 丢失锚点时，从 map 要点中用模糊匹配找回段号。"""
    best_ratio = 0.0
    best_anchor: int | None = None
    for point in points:
        anchor = _normalize_anchor(point.get("anchor"))
        if anchor is None:
            continue
        ratio = difflib.SequenceMatcher(None, text, str(point.get("text") or "")).ratio()
        if ratio > best_ratio:
            best_ratio, best_anchor = ratio, anchor
    return best_anchor if best_ratio >= 0.45 else None


def summarize(
    ctx: RunContext, refine: RefineResult, chapters: list[Chapter]
) -> tuple[SummaryResult, LlmClient]:
    client = LlmClient(ctx.settings.llm)
    model = ctx.settings.llm.model_for("summary")
    chunks = chunk_by_chars(
        refine.segments,
        max_chars=ctx.settings.llm.chunk_chars,
        get_text=lambda seg: seg.refined_text,  # type: ignore[attr-defined]
    )
    total = len(chunks)

    # ---- map：逐块提炼要点 ----
    points: list[dict[str, Any]] = []
    for position, chunk in enumerate(chunks, start=1):
        ctx.raise_if_cancelled()
        ctx.emit(
            "progress",
            step="summarize",
            done=position - 1,
            total=total + 1,
            message=f"提炼要点 {position}/{total} 块…",
        )
        numbered = "\n".join(f"[{seg.idx}] {seg.refined_text}" for seg in chunk)
        payload = client.complete_json(
            map_summary_prompt(numbered), system=MAP_SYSTEM, model=model
        )
        for item in payload.get("points") or []:
            if isinstance(item, dict) and str(item.get("text") or "").strip():
                points.append({"text": str(item["text"]).strip(), "anchor": item.get("anchor")})
    if not points:
        ctx.log.warning("map 阶段未提炼到任何要点，摘要将可能为空")

    ctx.emit(
        "progress",
        step="summarize",
        done=total,
        total=total + 1,
        message="汇总为结论与要点…",
    )

    # ---- reduce：合并去重 ----
    chapters_hint = ""
    if chapters:
        chapters_hint = "章节信息（chapter_index / title）：\n" + json.dumps(
            [{"chapter_index": ch.index, "title": ch.title} for ch in chapters],
            ensure_ascii=False,
        )
    payload = client.complete_json(
        reduce_summary_prompt(json.dumps(points, ensure_ascii=False), chapters_hint),
        system=REDUCE_SYSTEM,
        model=model,
    )

    if os.environ.get("VIDAR_DEBUG_LLM"):
        ctx.artifact("debug-reduce.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    # 模型偶尔会省略 figures：追加一次强约束请求（仅当整片一个配图锚点都没有）
    if chapters and not _payload_has_figures(payload):
        ctx.log.info("reduce 未返回配图锚点，追加一次请求…")
        stronger = reduce_summary_prompt(
            json.dumps(points, ensure_ascii=False), chapters_hint
        ) + (
            "\n\n补充要求：每个章节对象都必须包含 figures 字段；"
            "只要该章存在演示/实物/操作/画面，就必须给出至少 1 项"
            "（anchor 取给定要点的段号，caption 为 ≤20 字图注）。"
        )
        try:
            retry = client.complete_json(stronger, system=REDUCE_SYSTEM, model=model)
            if _payload_has_figures(retry):
                payload = retry
        except Exception as exc:  # noqa: BLE001 - 补充请求失败不影响主流程
            ctx.log.warning("配图锚点补充请求失败：%s", exc)

    start_by_index = {seg.idx: seg.start for seg in refine.segments}

    def to_start(anchor: int | None) -> float | None:
        if anchor is not None and anchor in start_by_index:
            return start_by_index[anchor]
        return None

    key_points: list[KeyPoint] = []
    for item in payload.get("key_points") or []:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        start = to_start(_normalize_anchor(item.get("anchor")))
        if start is None:
            # reduce 丢失锚点时，用模糊匹配从 map 要点中找回
            recovered = _recover_anchor(text, points)
            start = to_start(recovered)
            if start is not None:
                ctx.log.debug("要点锚点已通过模糊匹配恢复：%s", text[:20])
        key_points.append(KeyPoint(text=text, start=start))

    summaries: dict[int, str] = {}
    quotes: dict[int, str] = {}
    figures: dict[int, list[tuple[int, str]]] = {}
    chapter_items = payload.get("chapters") or payload.get("chapter_summaries") or []
    for item in chapter_items:
        if not isinstance(item, dict):
            continue
        index = item.get("chapter_index")
        if not isinstance(index, int):
            continue
        summaries[index] = _clean_summary(str(item.get("summary") or "").strip())
        quotes[index] = str(item.get("quote") or "").strip()
        collected: list[tuple[int, str]] = []
        for figure in item.get("figures") or []:
            if not isinstance(figure, dict):
                continue
            anchor = _normalize_anchor(figure.get("anchor"))
            caption = str(figure.get("caption") or "").strip()
            if anchor is not None and anchor in start_by_index:
                collected.append((anchor, caption))
        figures[index] = collected[:3]  # 每章最多 3 张

    merged_chapters = [
        Chapter(
            index=ch.index,
            title=ch.title,
            start=ch.start,
            end=ch.end,
            summary=summaries.get(ch.index, ""),
            quote=quotes.get(ch.index, ""),
            figures=[
                FigureHint(start=start_by_index[anchor], caption=caption, anchor=anchor)
                for anchor, caption in figures.get(ch.index, [])
            ],
        )
        for ch in chapters
    ]

    total_figures = sum(len(ch.figures) for ch in merged_chapters)
    max_figures = ctx.settings.export.max_images
    if total_figures > max_figures:
        ctx.log.warning("配图锚点过多（%d > %d），已截断", total_figures, max_figures)
        kept = 0
        for chapter in merged_chapters:
            room = max(0, max_figures - kept)
            chapter.figures = chapter.figures[:room]
            kept += len(chapter.figures)

    ctx.emit("progress", step="summarize", done=total + 1, total=total + 1, message="摘要完成")
    return (
        SummaryResult(
            model=model,
            one_liner=str(payload.get("one_liner") or "").strip(),
            key_points=key_points,
            chapters=merged_chapters,
            tokens_in=client.usage.prompt_tokens,
            tokens_out=client.usage.completion_tokens,
        ),
        client,
    )

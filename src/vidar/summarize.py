"""摘要提炼（map-reduce）：精修稿 → 一句话结论 + 核心要点 + 章节小结。

时间锚点全程本地映射（LLM 只输出段号），保证可追溯、杜绝时间戳幻觉。
reduce 丢失锚点时，用模糊匹配（difflib）从 map 要点里找回。
"""

from __future__ import annotations

import difflib
import json
import os
import re
from typing import TYPE_CHECKING, Any

from .errors import PauseRelease
from .llm.client import LlmClient
from .llm.prompts import (
    MAP_SYSTEM,
    REDUCE_SYSTEM,
    map_summary_prompt,
    reduce_summary_prompt,
)
from .models import Chapter, FigureHint, KeyPoint, RefineResult, SummaryResult
from .utils.checkpoint import clear_checkpoint, load_checkpoint, save_checkpoint
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

_ANCHOR_NUM = re.compile(r"\d+")


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
    """容忍 LLM 返回的各种锚点格式：整数 / 浮点 / 字符串数字 / 区间（"71-85" 取 71）。"""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        match = _ANCHOR_NUM.search(value)
        if match:
            return int(match.group())
    return None


def _recover_anchor(text: str, points: list[dict[str, Any]]) -> int | None:
    """reduce 丢失锚点时，从 map 要点中找回段号（相似度 + 二元组重叠）。"""
    best_score = 0.0
    best_anchor: int | None = None
    for point in points:
        anchor = _normalize_anchor(point.get("anchor"))
        if anchor is None:
            continue
        score = _point_match_score(text, str(point.get("text") or ""))
        if score > best_score:
            best_score, best_anchor = score, anchor
    return best_anchor if best_score >= 0.35 else None


def _char_bigrams(text: str) -> set[str]:
    cleaned = "".join(ch for ch in text if not ch.isspace())
    if len(cleaned) < 2:
        return {cleaned} if cleaned else set()
    return {cleaned[i : i + 2] for i in range(len(cleaned) - 1)}


def _point_match_score(text: str, other: str) -> float:
    """要点文本相似度：difflib 比率与字符二元组重叠各占一半。"""
    if not text or not other:
        return 0.0
    ratio = difflib.SequenceMatcher(None, text, other).ratio()
    left, right = _char_bigrams(text), _char_bigrams(other)
    overlap = len(left & right) / max(1, min(len(left), len(right)))
    return 0.5 * ratio + 0.5 * overlap


def _fill_missing_starts(key_points: list[KeyPoint], ctx: RunContext) -> None:
    """仍缺失锚点的要点：按相邻已定位要点等距插值，保证每个要点都有画面时刻。"""
    total = len(key_points)
    known = [index for index in range(total) if key_points[index].start is not None]
    if not known or len(known) == total:
        return
    filled = 0
    for left, right in zip([-1, *known], [*known, total], strict=True):
        missing = list(range(left + 1, right))
        if not missing:
            continue
        left_start = key_points[left].start if left >= 0 else None
        right_start = key_points[right].start if right < total else None
        for order, index in enumerate(missing, start=1):
            if left_start is not None and right_start is not None:
                step = (right_start - left_start) / (len(missing) + 1)
                key_points[index].start = left_start + step * order
            elif left_start is not None:
                key_points[index].start = left_start + 60.0 * order
            elif right_start is not None:
                key_points[index].start = max(
                    0.0, right_start - 60.0 * (len(missing) + 1 - order)
                )
            filled += 1
    if filled:
        ctx.log.info("要点时间戳补全：%d 个要点按相邻位置插值（供要点截图）", filled)


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

    # ---- map：逐块提炼要点（带检查点，可暂停续跑） ----
    partial_path = ctx.artifact("summarize.partial.json")
    partial = load_checkpoint(partial_path) or {}
    points: list[dict[str, Any]] = [
        item for item in (partial.get("points") or []) if isinstance(item, dict)
    ]
    map_done = int(partial.get("map_done") or 0)
    if map_done:
        ctx.log.info("检测到摘要检查点：map 已完成 %d/%d 块", map_done, total)

    for position, chunk in enumerate(chunks, start=1):
        if position <= map_done:
            continue  # 该块已完成（续跑跳过）
        ctx.raise_if_cancelled()
        try:
            ctx.wait_if_paused()
        except PauseRelease:
            save_checkpoint(partial_path, {"points": points, "map_done": position - 1})
            raise
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
        save_checkpoint(partial_path, {"points": points, "map_done": position})
    if not points:
        ctx.log.warning("map 阶段未提炼到任何要点，摘要将可能为空")
    anchored = sum(1 for point in points if _normalize_anchor(point.get("anchor")) is not None)
    ctx.log.info("map 要点：%d 条（带锚点 %d 条）", len(points), anchored)
    if os.environ.get("VIDAR_DEBUG_LLM"):
        ctx.artifact("debug-map.json").write_text(
            json.dumps(points, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    ctx.emit(
        "progress",
        step="summarize",
        done=total,
        total=total + 1,
        message="汇总为结论与要点…",
    )
    ctx.raise_if_cancelled()
    try:
        ctx.wait_if_paused()
    except PauseRelease:
        save_checkpoint(partial_path, {"points": points, "map_done": total})
        raise

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
    recovered = 0
    for item in payload.get("key_points") or []:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        start = to_start(_normalize_anchor(item.get("anchor")))
        if start is None:
            # reduce 丢失锚点时，用模糊匹配从 map 要点中找回
            recovered_anchor = _recover_anchor(text, points)
            start = to_start(recovered_anchor)
            if start is not None:
                recovered += 1
                ctx.log.debug("要点锚点已通过模糊匹配恢复：%s", text[:20])
        key_points.append(KeyPoint(text=text, start=start))
    if key_points:
        direct = sum(1 for point in key_points if point.start is not None) - recovered
        ctx.log.info(
            "要点时间戳：直接命中 %d / 模糊恢复 %d / 共 %d", direct, recovered, len(key_points)
        )
    _fill_missing_starts(key_points, ctx)

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
    clear_checkpoint(partial_path)
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

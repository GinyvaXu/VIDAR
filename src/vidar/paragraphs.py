"""语段编辑（LLM）：零散句子 → 自然段（只决定边界，不改写文字）。

产物 work/<key>/paragraphs.json：
    {model, paragraphs: [{index, start, end, text, source_text, segments}]}

设计：
- 有译文时按译文编组（文档语言 = 目标语言），source_text 存对应原文（双语对照用）；
- 段号连续覆盖校验：缺/重/越界 → 追加一次严格请求；仍失败 → 按长度机械编组兜底；
- 统一做「过短合并 / 过长拆分」边界修正，保证一段不太长也不太短。
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any

from .llm.client import LlmClient
from .llm.prompts import PARAGRAPHS_SYSTEM, paragraphs_prompt
from .models import ParagraphGroup, ParagraphResult, RefineResult, TranslateResult
from .translate import translation_map

if TYPE_CHECKING:
    from .pipeline.context import RunContext

_ROW = tuple[int, float, float, str, str]  # (idx, start, end, 正文, 原文)


def _effective_rows(
    refine: RefineResult, translation: TranslateResult | None
) -> list[_ROW]:
    """编组用的行：正文取译文（有则用），原文始终保留（双语对照）。"""
    mapping = translation_map(translation)
    rows: list[_ROW] = []
    for seg in refine.segments:
        text = mapping.get(seg.idx) or seg.refined_text
        rows.append((seg.idx, seg.start, seg.end, text, seg.refined_text))
    return rows


def _mechanical_groups(rows: list[_ROW], *, target_chars: int = 150) -> list[list[int]]:
    """兜底：按目标字数在句边界贪心编组。"""
    groups: list[list[int]] = []
    current: list[int] = []
    size = 0
    for idx, _start, _end, text, _source in rows:
        if current and size + len(text) > target_chars:
            groups.append(current)
            current, size = [], 0
        current.append(idx)
        size += len(text)
    if current:
        groups.append(current)
    return groups


def _parse_groups(payload: dict[str, Any], valid: list[int]) -> list[list[int]] | None:
    """解析并校验 LLM 返回的分组：必须连续覆盖全部段号（不重不漏）。"""
    items = payload.get("paragraphs")
    if not isinstance(items, list) or not items:
        return None
    position = {idx: order for order, idx in enumerate(valid)}
    groups: list[list[int]] = []
    for item in items:
        if not isinstance(item, dict):
            return None
        start = item.get("start_index", item.get("start"))
        end = item.get("end_index", item.get("end"))
        if isinstance(start, bool) or isinstance(end, bool):
            return None
        if not isinstance(start, int) or not isinstance(end, int):
            return None
        if start not in position or end not in position:
            return None
        if position[end] < position[start]:
            return None
        groups.append(valid[position[start] : position[end] + 1])
    flat = [idx for group in groups for idx in group]
    return groups if flat == valid else None


def _split_balanced(
    group: list[int], rows_by_idx: dict[int, _ROW], *, max_chars: int
) -> list[list[int]]:
    """过长段按「均衡份数」在句边界拆分（避免拆出短尾巴）。

    切分时机：加入下一句会超过上限（先切），或已达到均衡目标长度（提前切）。
    """
    total = sum(len(rows_by_idx[idx][3]) for idx in group)
    if total <= max_chars or len(group) == 1:
        return [group]
    parts = max(2, math.ceil(total / max_chars))
    target = total / parts
    chunks: list[list[int]] = []
    current: list[int] = []
    size = 0
    remaining_parts = parts
    for position, idx in enumerate(group):
        text_len = len(rows_by_idx[idx][3])
        remaining_segments = len(group) - position - 1
        should_cut = (
            current
            and remaining_parts > 1
            and (size >= target or size + text_len > max_chars)
            and remaining_segments >= remaining_parts - 1
        )
        if should_cut:
            chunks.append(current)
            current, size = [], 0
            remaining_parts -= 1
        current.append(idx)
        size += text_len
    if current:
        chunks.append(current)
    return chunks


def _enforce_bounds(
    groups: list[list[int]],
    rows_by_idx: dict[int, _ROW],
    *,
    min_chars: int,
    max_chars: int,
) -> list[list[int]]:
    """边界修正：过短段并入相邻段（优先较短一侧）；过长段均衡拆分；再合并短尾。"""

    def length(group: list[int]) -> int:
        return sum(len(rows_by_idx[idx][3]) for idx in group)

    def merge_short(items: list[list[int]]) -> list[list[int]]:
        merged = True
        while merged and len(items) > 1:
            merged = False
            for index, group in enumerate(items):
                if length(group) >= min_chars:
                    continue
                if index > 0 and (
                    index == len(items) - 1
                    or length(items[index - 1]) <= length(items[index + 1])
                ):
                    items[index - 1].extend(group)
                else:
                    items[index + 1] = group + items[index + 1]
                items.pop(index)
                merged = True
                break
        return items

    result = merge_short([list(group) for group in groups])
    split: list[list[int]] = []
    for group in result:
        split.extend(_split_balanced(group, rows_by_idx, max_chars=max_chars))
    return merge_short(split)


def build_paragraphs(
    ctx: RunContext,
    refine: RefineResult,
    translation: TranslateResult | None,
) -> ParagraphResult:
    """LLM 编组 + 校验 + 兜底 + 边界修正，返回 ParagraphResult。"""
    settings = ctx.settings.paragraph
    rows = _effective_rows(refine, translation)
    if not rows:
        return ParagraphResult(model="")

    valid = [row[0] for row in rows]
    rows_by_idx = {row[0]: row for row in rows}
    total_chars = sum(len(row[3]) for row in rows)

    client = LlmClient(ctx.settings.llm)
    model = ctx.settings.llm.model_for("paragraph")
    numbered = "\n".join(f"[{idx}] {text}" for idx, _start, _end, text, _src in rows)

    ctx.emit("progress", step="paragraphs", done=0, total=1, message="请求语段编辑…")
    ctx.raise_if_cancelled()
    ctx.wait_if_paused()
    payload = client.complete_json(
        paragraphs_prompt(
            numbered, min_chars=settings.min_chars, max_chars=settings.max_chars
        ),
        system=PARAGRAPHS_SYSTEM,
        model=model,
    )
    groups = _parse_groups(payload, valid)
    if groups is None:
        ctx.log.info("语段编辑结果不合法，追加一次严格请求…")
        try:
            retry = client.complete_json(
                paragraphs_prompt(
                    numbered,
                    min_chars=settings.min_chars,
                    max_chars=settings.max_chars,
                    strict=True,
                ),
                system=PARAGRAPHS_SYSTEM,
                model=model,
            )
            groups = _parse_groups(retry, valid)
        except Exception as exc:  # noqa: BLE001 - 补充请求失败不影响主流程
            ctx.log.warning("语段编辑严格请求失败：%s", exc)
    if groups is None:
        ctx.log.warning("语段编辑不可用，改用按长度机械编组（%d 字参考）", 150)
        groups = _mechanical_groups(rows)

    groups = _enforce_bounds(
        groups,
        rows_by_idx,
        min_chars=settings.min_chars,
        max_chars=settings.max_chars,
    )

    paragraphs: list[ParagraphGroup] = []
    for index, group in enumerate(groups):
        first = rows_by_idx[group[0]]
        last = rows_by_idx[group[-1]]
        paragraphs.append(
            ParagraphGroup(
                index=index,
                start=first[1],
                end=last[2],
                text="".join(rows_by_idx[idx][3] for idx in group),
                source_text="".join(rows_by_idx[idx][4] for idx in group),
                segments=group,
            )
        )

    ctx.emit(
        "progress",
        step="paragraphs",
        done=1,
        total=1,
        message=f"共 {len(paragraphs)} 段（{total_chars} 字）",
    )
    return ParagraphResult(
        model=model,
        tokens_in=client.usage.prompt_tokens,
        tokens_out=client.usage.completion_tokens,
        paragraphs=paragraphs,
    )


def load_paragraphs(ctx: RunContext) -> ParagraphResult | None:
    """读取 paragraphs.json（不存在/损坏时返回 None → 渲染层回退逐段）。"""
    path = ctx.artifact("paragraphs.json")
    if not path.exists():
        return None
    try:
        return ParagraphResult.model_validate_json(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None


__all__ = ["build_paragraphs", "load_paragraphs"]

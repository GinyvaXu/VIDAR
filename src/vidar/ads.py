"""广告识别（LLM 段级）：refine.json → ads.json（被标记的段号区间）。

契约：
- 只标记「真广告」（赞助/带货/商务植入/收费推广口播），宁缺毋滥；
- 段号区间为 refine / asr 同一套 1:1 段号；时间戳由本地映射（LLM 不接触时间数字）；
- 下游统一经 `load_refine_effective()` 消费「去掉广告、顺序重编号」的精修段：
  翻译 / 章节 / 摘要 / 语段编辑 / 导出与重渲染因此全部自动省略广告；
- 原始逐字稿同样按区间省略；被省略内容由导出层另存「省略广告版-*.txt」记录留档。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .errors import LlmError, PauseRelease, VidarError
from .llm.client import LlmClient
from .llm.prompts import ADS_SYSTEM, ads_prompt
from .models import AdRange, AdsResult, AsrResult, RefineResult
from .utils.checkpoint import clear_checkpoint, load_checkpoint, save_checkpoint
from .utils.text import chunk_by_chars, format_clock

if TYPE_CHECKING:
    from .pipeline.context import RunContext


# --------------------------------------------------------------------------- #
# 解析与合并（纯函数，便于单测）
# --------------------------------------------------------------------------- #
def _as_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value.strip())
    return None


def _parse_ranges(payload: dict) -> list[tuple[int, int, str]]:
    """解析 LLM 返回 {"ads": [{start, end, reason}]}，容忍键名/类型/顺序变体。"""
    for key in ("ads", "ranges", "ad"):
        if key in payload:
            items = payload.get(key)
            break
    else:
        raise LlmError("广告识别返回格式不正确（缺少 ads 数组）", hint=str(payload)[:200])
    if not isinstance(items, list):
        raise LlmError("广告识别返回格式不正确（ads 不是数组）", hint=str(payload)[:200])
    ranges: list[tuple[int, int, str]] = []
    for item in items:
        if isinstance(item, dict):
            start = _as_int(item.get("start", item.get("start_index")))
            end = _as_int(item.get("end", item.get("end_index")))
            reason = str(item.get("reason") or item.get("note") or "").strip()
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            start, end = _as_int(item[0]), _as_int(item[1])
            reason = ""
        else:
            continue
        if start is None or end is None:
            continue
        if start > end:
            start, end = end, start
        ranges.append((start, end, reason[:60]))
    return ranges


def merge_ranges(ranges: list[tuple[int, int, str]]) -> list[tuple[int, int, str]]:
    """排序并按「重叠」合并（相邻但不重叠的保留为两条，保留各自理由）。"""
    ordered = sorted(ranges, key=lambda item: (item[0], item[1]))
    merged: list[tuple[int, int, str]] = []
    for start, end, reason in ordered:
        if merged and start <= merged[-1][1]:
            prev_start, prev_end, prev_reason = merged[-1]
            merged[-1] = (prev_start, max(prev_end, end), prev_reason or reason)
        else:
            merged.append((start, end, reason))
    return merged


# --------------------------------------------------------------------------- #
# 检测（LLM，逐块 + 检查点）
# --------------------------------------------------------------------------- #
def detect_ads(ctx: RunContext, refine: RefineResult) -> AdsResult:
    """逐块识别广告段号区间；暂停可续跑（按块检查点 ads.partial.json）。"""
    client = LlmClient(ctx.settings.llm)
    model = ctx.settings.llm.model_for("ads")
    segments = refine.segments
    if not segments:
        return AdsResult(model=model)

    lo = min(seg.idx for seg in segments)
    hi = max(seg.idx for seg in segments)
    by_idx = {seg.idx: seg for seg in segments}

    chunks = chunk_by_chars(
        segments, max_chars=ctx.settings.llm.chunk_chars, get_text=lambda seg: seg.refined_text
    )
    total = len(chunks)

    partial_path = ctx.artifact("ads.partial.json")
    partial = load_checkpoint(partial_path) or {}
    raw: list[tuple[int, int, str]] = []
    for item in partial.get("ranges") or []:
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            start, end = _as_int(item[0]), _as_int(item[1])
            if start is not None and end is not None:
                raw.append((start, end, str(item[2]) if len(item) > 2 else ""))
    done_blocks = int(partial.get("done_blocks") or 0)
    if done_blocks:
        ctx.log.info("检测到广告识别检查点：已完成 %d/%d 块，续跑中", done_blocks, total)

    for position, chunk in enumerate(chunks, start=1):
        if position <= done_blocks:
            continue
        ctx.raise_if_cancelled()
        try:
            ctx.wait_if_paused()
        except PauseRelease:
            save_checkpoint(partial_path, {"ranges": raw, "done_blocks": position - 1})
            raise
        ctx.emit(
            "progress",
            step="ads",
            done=position - 1,
            total=total,
            message=f"广告识别第 {position}/{total} 块…",
        )
        numbered = "\n".join(f"[{seg.idx}] {seg.refined_text}" for seg in chunk)
        try:
            payload = client.complete_json(ads_prompt(numbered), system=ADS_SYSTEM, model=model)
        except VidarError as exc:
            # 单块失败不炸整步：该块不参与识别（日志可见，重跑本步可重试）
            ctx.log.warning("第 %d/%d 块广告识别失败（跳过该块）：%s", position, total, exc)
            continue
        raw.extend(_parse_ranges(payload))
        save_checkpoint(partial_path, {"ranges": raw, "done_blocks": position})

    # 本地校验：裁剪到有效段号范围，再合并重叠
    cleaned = [
        (max(lo, min(hi, start)), max(lo, min(hi, end)), reason)
        for start, end, reason in raw
        if lo <= end and start <= hi
    ]
    ranges: list[AdRange] = []
    for start, end, reason in merge_ranges(cleaned):
        first = by_idx.get(start) or min(segments, key=lambda seg: abs(seg.idx - start))
        last = by_idx.get(end) or min(segments, key=lambda seg: abs(seg.idx - end))
        ranges.append(
            AdRange(start_idx=start, end_idx=end, start=first.start, end=last.end, reason=reason)
        )

    clear_checkpoint(partial_path)
    ctx.emit("progress", step="ads", done=total, total=total, message="广告识别完成")
    return AdsResult(
        model=model,
        tokens_in=client.usage.prompt_tokens,
        tokens_out=client.usage.completion_tokens,
        ranges=ranges,
    )


# --------------------------------------------------------------------------- #
# 读取与应用
# --------------------------------------------------------------------------- #
def load_ads(ctx: RunContext) -> AdsResult | None:
    """读取 ads.json（损坏或不存在返回 None，视为无广告）。"""
    path = ctx.artifact("ads.json")
    if not path.exists():
        return None
    try:
        return AdsResult.model_validate_json(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None


def ads_range_key(ads: AdsResult | None) -> list[tuple[int, int]]:
    """用于判断广告区间是否变化（变化时需要清理下游按块检查点）。"""
    if ads is None:
        return []
    return [(item.start_idx, item.end_idx) for item in ads.ranges]


def ad_index_set(ads: AdsResult | None) -> set[int]:
    if ads is None:
        return set()
    return {
        idx
        for item in ads.ranges
        for idx in range(item.start_idx, item.end_idx + 1)
    }


def apply_ads(refine: RefineResult, ads: AdsResult | None) -> RefineResult:
    """精修稿「有效视图」：去掉广告段并顺序重编号（0..M-1，时间戳/文本保持）。"""
    skipped = ad_index_set(ads)
    if not skipped:
        return refine
    segments = [
        seg.model_copy(update={"idx": position})
        for position, seg in enumerate(seg for seg in refine.segments if seg.idx not in skipped)
    ]
    return refine.model_copy(update={"segments": segments})


def apply_ads_to_asr(asr: AsrResult, ads: AdsResult | None) -> AsrResult:
    """原始 ASR 段去掉广告段（段号保持原值，供原始逐字稿渲染）。"""
    skipped = ad_index_set(ads)
    if not skipped:
        return asr
    return asr.model_copy(
        update={"segments": [seg for seg in asr.segments if seg.idx not in skipped]}
    )


def load_refine_effective(ctx: RunContext) -> RefineResult:
    """下游统一切口：加载精修稿并应用广告过滤（无 ads.json 时原样返回）。"""
    path = ctx.artifact("refine.json")
    if not path.exists():
        raise VidarError(
            "缺少中间产物 refine.json",
            hint="请先运行前置步骤：resolve → download → audio → asr → refine",
        )
    refine = RefineResult.model_validate_json(path.read_text(encoding="utf-8"))
    return apply_ads(refine, load_ads(ctx))


def ads_note_text(ads: AdsResult | None) -> str:
    """文末一句总结（无广告时返回空串）。"""
    if ads is None or not ads.ranges:
        return ""
    seconds = sum(item.duration_sec for item in ads.ranges)
    return f"本稿已自动省略 {len(ads.ranges)} 段广告（合计 {format_clock(seconds)}）。"


__all__ = [
    "ad_index_set",
    "ads_note_text",
    "ads_range_key",
    "apply_ads",
    "apply_ads_to_asr",
    "detect_ads",
    "load_ads",
    "load_refine_effective",
    "merge_ranges",
]

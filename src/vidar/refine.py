"""文稿精修（LLM 段级）。

契约：输入 ASR 合并段（带时间戳），输出精修段，段号与时间戳 1:1 保持。
安全网：缺失 / 明显被压缩的段落自动回退原文，绝不丢内容。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .errors import LlmError, VidarError
from .llm.client import LlmClient
from .llm.prompts import REFINE_SYSTEM, refine_prompt
from .models import AsrResult, RefinedSegment, RefineResult
from .utils.text import chunk_by_chars

if TYPE_CHECKING:
    from .pipeline.context import RunContext

_MIN_RATIO = 0.5  # 精修后短于原文 50% 视为"被概括"，回退原文


def refine_asr_result(ctx: RunContext, asr: AsrResult) -> RefineResult:
    client = LlmClient(ctx.settings.llm)
    model = ctx.settings.llm.model_for("refine")
    chunks = chunk_by_chars(asr.segments, max_chars=ctx.settings.llm.chunk_chars)
    total = len(chunks)

    refined_map: dict[int, str] = {}
    for position, chunk in enumerate(chunks, start=1):
        ctx.raise_if_cancelled()
        ctx.emit(
            "progress",
            step="refine",
            done=position - 1,
            total=total,
            message=f"精修第 {position}/{total} 块…",
        )
        numbered = "\n".join(f"[{seg.idx}] {seg.text}" for seg in chunk)
        payload = client.complete_json(
            refine_prompt(numbered), system=REFINE_SYSTEM, model=model
        )
        items = payload.get("refined")
        if not isinstance(items, list):
            raise LlmError("精修返回格式不正确（缺少 refined 数组）", hint=str(payload)[:200])
        for item in items:
            if not isinstance(item, dict):
                continue
            index = item.get("index")
            text = item.get("text")
            if isinstance(index, int) and isinstance(text, str) and text.strip():
                refined_map[index] = text.strip()

    # 补修：一次追加请求处理主请求漏掉的段落
    missing = [seg for seg in asr.segments if seg.idx not in refined_map]
    if missing:
        ctx.log.info("补修 %d 段未返回结果的段落", len(missing))
        numbered = "\n".join(f"[{seg.idx}] {seg.text}" for seg in missing)
        try:
            payload = client.complete_json(
                refine_prompt(numbered), system=REFINE_SYSTEM, model=model
            )
            for item in payload.get("refined") or []:
                if not isinstance(item, dict):
                    continue
                index = item.get("index")
                text = item.get("text")
                if isinstance(index, int) and isinstance(text, str) and text.strip():
                    refined_map[index] = text.strip()
        except VidarError as exc:
            ctx.log.warning("补修失败（不影响主流程）：%s", exc)

    segments: list[RefinedSegment] = []
    for seg in asr.segments:
        refined = refined_map.get(seg.idx)
        if refined is None:
            ctx.log.warning("第 %d 段未获得精修结果，已保留原文", seg.idx)
            refined = seg.text
        elif len(refined) < len(seg.text) * _MIN_RATIO:
            ctx.log.warning("第 %d 段精修结果异常偏短，已保留原文", seg.idx)
            refined = seg.text
        segments.append(
            RefinedSegment(
                idx=seg.idx,
                start=seg.start,
                end=seg.end,
                raw_text=seg.text,
                refined_text=refined,
            )
        )

    ctx.emit("progress", step="refine", done=total, total=total, message="精修完成")
    return RefineResult(
        model=model,
        tokens_in=client.usage.prompt_tokens,
        tokens_out=client.usage.completion_tokens,
        segments=segments,
    )

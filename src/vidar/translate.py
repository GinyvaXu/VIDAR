"""逐段翻译（LLM）：精修稿 → 目标语言译文（1:1 段号映射），供双语对照。

产物 work/<key>/translate.json：
    {model, source_language, target_language,
     segments: [{idx, start, end, source_text, translated_text}]}

设计约定：
- 段号 1:1 对应：异常/缺失段落回退为空译文（渲染层自动退回源文本），下游永不缺段；
- 按 translate.max_chars 分块请求，块内一次 JSON 返回；缺段自动重试一次；
- 源语言即目标语言（如中文视频 target=zh）时整体跳过（不写产物）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .errors import PauseRelease
from .llm.client import LlmClient
from .llm.prompts import TRANSLATE_SYSTEM, translate_prompt
from .models import RefineResult, TranslatedSegment, TranslateResult
from .utils.checkpoint import clear_checkpoint, load_checkpoint, save_checkpoint
from .utils.text import chunk_by_chars

if TYPE_CHECKING:
    from .pipeline.context import RunContext

# 语言代码 → 提示词中的语言名（覆盖常见语种，未知代码原样使用）
_LANGUAGE_NAMES = {
    "zh": "简体中文",
    "en": "英文",
    "ja": "日文",
    "ko": "韩文",
    "fr": "法文",
    "de": "德文",
    "es": "西班牙文",
    "ru": "俄文",
    "pt": "葡萄牙文",
    "it": "意大利文",
    "ar": "阿拉伯文",
    "th": "泰文",
    "vi": "越南文",
    "id": "印尼文",
    "tr": "土耳其文",
}


def normalize_language(code: str) -> str:
    """语言代码归一化：zh-cn / Chinese / zh_Hans → zh。"""
    value = (code or "").strip().lower().replace("_", "-")
    if not value or value in ("auto", "detect", "unknown"):
        return ""
    if value.startswith("zh") or value in ("chinese", "cmn"):
        return "zh"
    return value.split("-")[0]


def language_name(code: str) -> str:
    normalized = normalize_language(code)
    return _LANGUAGE_NAMES.get(normalized, normalized or "未知语言")


def should_translate(
    source_language: str, target_language: str, *, skip_same: bool = True
) -> bool:
    """源语言与目标语言不一致时需要翻译；检测不到源语言时保守翻译。"""
    source = normalize_language(source_language)
    target = normalize_language(target_language) or "zh"
    if not skip_same:
        return True
    if not source:
        return False  # 无法判断源语言时跳过（避免把中文再翻译一遍）
    return source != target


def _collect_translations(payload: dict[str, Any]) -> dict[int, str]:
    """解析 LLM 返回：兼容 idx/index 与 text/translation 等键名。"""
    collected: dict[int, str] = {}
    items = payload.get("segments") or payload.get("translations") or []
    if not isinstance(items, list):
        return collected
    for item in items:
        if not isinstance(item, dict):
            continue
        raw_index = item.get("idx", item.get("index"))
        if isinstance(raw_index, bool):
            continue
        try:
            index = int(raw_index)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            continue
        text = str(item.get("text") or item.get("translation") or "").strip()
        if text:
            collected[index] = text
    return collected


def translate_segments(
    ctx: RunContext,
    refine: RefineResult,
    source_language: str,
) -> TranslateResult:
    """把精修稿逐段翻译为目标语言，返回 TranslateResult（1:1 段号）。"""
    settings = ctx.settings.translate
    client = LlmClient(ctx.settings.llm)
    model = ctx.settings.llm.model_for("translate")
    source_name = language_name(source_language)
    target_name = language_name(settings.target_language)

    chunks = chunk_by_chars(
        refine.segments,
        max_chars=max(600, settings.max_chars),
        get_text=lambda seg: f"[{seg.idx}] {seg.refined_text}",  # type: ignore[attr-defined]
    )
    total = len(chunks)
    ctx.log.info(
        "翻译：%s → %s，共 %d 段 / %d 块（模型 %s）",
        source_name,
        target_name,
        len(refine.segments),
        total,
        model,
    )

    translated: dict[int, str] = {}
    partial_path = ctx.artifact("translate.partial.json")
    partial = load_checkpoint(partial_path) or {}
    translated = {
        int(key): str(value)
        for key, value in (partial.get("translated") or {}).items()
        if str(value).strip()
    }
    if translated:
        ctx.log.info("检测到翻译检查点：已完成 %d 段，续跑中", len(translated))

    for position, chunk in enumerate(chunks, start=1):
        if all(seg.idx in translated for seg in chunk):
            continue  # 该块已完成（续跑跳过）
        ctx.raise_if_cancelled()
        try:
            ctx.wait_if_paused()
        except PauseRelease:
            save_checkpoint(partial_path, {"translated": translated})
            raise
        numbered = "\n".join(f"[{seg.idx}] {seg.refined_text}" for seg in chunk)
        ctx.emit(
            "progress",
            step="translate",
            done=position - 1,
            total=total,
            message=f"翻译 {position}/{total} 块（{source_name}→{target_name}）",
        )
        payload = client.complete_json(
            translate_prompt(numbered, source_name, target_name),
            system=TRANSLATE_SYSTEM,
            model=model,
        )
        got = _collect_translations(payload)
        missing = [seg for seg in chunk if seg.idx not in got]
        if missing:
            ctx.log.info("第 %d 块缺 %d 段译文，追加一次请求", position, len(missing))
            retry_numbered = "\n".join(f"[{seg.idx}] {seg.refined_text}" for seg in missing)
            try:
                retry = client.complete_json(
                    translate_prompt(retry_numbered, source_name, target_name, strict=True),
                    system=TRANSLATE_SYSTEM,
                    model=model,
                )
                got.update(_collect_translations(retry))
            except Exception as exc:  # noqa: BLE001 - 补充请求失败不阻断
                ctx.log.warning("缺段补充请求失败：%s", exc)
        for seg in chunk:
            translated[seg.idx] = got.get(seg.idx, "")
        save_checkpoint(partial_path, {"translated": translated})

    blank = sum(1 for seg in refine.segments if not translated.get(seg.idx))
    if blank:
        ctx.log.warning("有 %d 段未获得译文（将回退显示原文）", blank)

    ctx.emit("progress", step="translate", done=total, total=total, message="翻译完成")
    clear_checkpoint(partial_path)
    return TranslateResult(
        model=model,
        source_language=normalize_language(source_language) or source_language,
        target_language=normalize_language(settings.target_language) or "zh",
        tokens_in=client.usage.prompt_tokens,
        tokens_out=client.usage.completion_tokens,
        segments=[
            TranslatedSegment(
                idx=seg.idx,
                start=seg.start,
                end=seg.end,
                source_text=seg.refined_text,
                translated_text=translated.get(seg.idx, ""),
            )
            for seg in refine.segments
        ],
    )


def load_translation(ctx: RunContext) -> TranslateResult | None:
    """读取 translate.json（不存在/损坏时返回 None）。"""
    path = ctx.artifact("translate.json")
    if not path.exists():
        return None
    try:
        return TranslateResult.model_validate_json(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None


def translation_map(translation: TranslateResult | None) -> dict[int, str]:
    if translation is None:
        return {}
    return {
        seg.idx: seg.translated_text
        for seg in translation.segments
        if seg.translated_text.strip()
    }


def apply_translation(refine: RefineResult, translation: TranslateResult | None) -> RefineResult:
    """产出「内容加工用」精修稿：有译文时用译文替换文本（段号/时间戳不变）。

    章节切分与摘要都基于目标语言文本进行，保证最终文档语言一致。
    """
    if translation is None:
        return refine
    mapping = translation_map(translation)
    if not mapping:
        return refine
    return refine.model_copy(
        update={
            "segments": [
                seg.model_copy(
                    update={"refined_text": mapping.get(seg.idx, seg.refined_text)}
                )
                for seg in refine.segments
            ]
        }
    )


__all__ = [
    "apply_translation",
    "language_name",
    "load_translation",
    "normalize_language",
    "should_translate",
    "translate_segments",
    "translation_map",
]

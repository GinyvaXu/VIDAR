"""Markdown / SRT 渲染：逐字稿、精修稿、notes.md、outline.md。"""

from __future__ import annotations

import json
from datetime import datetime

from .models import (
    AdsResult,
    AsrResult,
    RefineResult,
    SummaryResult,
    TranslateResult,
    VideoMeta,
)
from .utils.text import format_clock, format_srt_time


def _translation_map(
    translation: TranslateResult | None, bilingual: bool
) -> dict[int, str]:
    if translation is None or not bilingual:
        return {}
    return {
        seg.idx: seg.translated_text
        for seg in translation.segments
        if seg.translated_text.strip()
    }


def note_link(url: str, start: float | None) -> str:
    """生成 B 站带时间戳的跳转链接。"""
    if start is None:
        return ""
    joiner = "&" if "?" in url else "?"
    return f"{url}{joiner}t={int(start)}"


def render_asr_transcript(
    result: AsrResult, *, title: str = "原始逐字稿", ads_note: str = ""
) -> str:
    """原始逐字稿：每段带 [mm:ss] 时间戳，保留口语原貌（广告段已省略）。"""
    lines: list[str] = [
        f"# {title}",
        "",
        f"> ASR：{result.engine} / {result.model}　·　语言：{result.language}"
        f"　·　时长：{format_clock(result.duration_sec or 0)}",
        "",
    ]
    for seg in result.segments:
        lines.append(f"- **[{format_clock(seg.start)}]** {seg.text}")
    if ads_note:
        lines += ["", f"> {ads_note}"]
    lines.append("")
    return "\n".join(lines)


def render_refined_transcript(
    refine: RefineResult,
    *,
    title: str = "精修稿",
    translation: TranslateResult | None = None,
    bilingual: bool = False,
    ads_note: str = "",
) -> str:
    mapping = _translation_map(translation, bilingual)
    lines: list[str] = [
        f"# {title}",
        "",
        f"> 精修模型：{refine.model}　·　tokens：{refine.tokens_in} → {refine.tokens_out}",
        "",
    ]
    if mapping and translation is not None:
        lines += [
            f"> 双语对照：译文在上、原文在下（{translation.source_language} → "
            f"{translation.target_language}）",
            "",
        ]
    for seg in refine.segments:
        translated = mapping.get(seg.idx, "")
        if translated:
            lines.append(f"- **[{format_clock(seg.start)}]** {translated}")
            lines.append(f"  - *{seg.refined_text}*")
        else:
            lines.append(f"- **[{format_clock(seg.start)}]** {seg.refined_text}")
    if ads_note:
        lines += ["", f"> {ads_note}"]
    lines.append("")
    return "\n".join(lines)


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ").strip()


def render_notes(
    meta: VideoMeta, summary: SummaryResult, *, asr_model: str, ads_note: str = ""
) -> str:
    processed = datetime.now().strftime("%Y-%m-%d %H:%M")
    published = meta.pubdate.strftime("%Y-%m-%d") if meta.pubdate else ""
    frontmatter = [
        "---",
        f"title: {json.dumps(meta.title, ensure_ascii=False)}",
        f"bvid: {meta.bvid}",
        f"url: {meta.url}",
        f"up: {json.dumps(meta.up_name, ensure_ascii=False)}",
        f"published: {published}",
        f'duration: "{format_clock(meta.duration_sec or 0)}"',
        f"processed_at: {processed}",
        f'asr: "{asr_model}"',
        f'llm: "{summary.model}"',
        "---",
        "",
    ]
    body: list[str] = [f"# {meta.title}", ""]
    if summary.one_liner:
        body += [f"> **一句话结论：** {summary.one_liner}", ""]
    if summary.key_points:
        body += ["## 核心要点", ""]
        for number, point in enumerate(summary.key_points, start=1):
            link = note_link(meta.url, point.start)
            suffix = f"（[{format_clock(point.start or 0)}]({link})）" if link else ""
            body.append(f"{number}. {point.text}{suffix}")
        body.append("")
    if summary.chapters:
        body += ["## 章节导航", "", "| 时间 | 章节 | 小结 |", "|------|------|------|"]
        for chapter in summary.chapters:
            link = note_link(meta.url, chapter.start)
            body.append(
                f"| [{format_clock(chapter.start)}]({link}) | "
                f"{_cell(chapter.title)} | {_cell(chapter.summary)} |"
            )
        body += ["", "## 章节小结", ""]
        for chapter in summary.chapters:
            link = note_link(meta.url, chapter.start)
            body.append(
                f"### {chapter.index + 1}. {chapter.title}"
                f"（[{format_clock(chapter.start)}]({link})）"
            )
            if chapter.summary:
                body.append(f"- {chapter.summary}")
            body.append("")
    if ads_note:
        body += [f"> {ads_note}"]
    return "\n".join(frontmatter + body)


def render_outline(meta: VideoMeta, summary: SummaryResult) -> str:
    """确定性生成大纲：结论 → 章节 → 归属要点（按时间区间分配）。"""
    lines: list[str] = [f"# {meta.title}", ""]
    if summary.one_liner:
        lines += [f"> {summary.one_liner}", ""]

    chapter_points: dict[int, list[str]] = {}
    unassigned: list[str] = []
    for point in summary.key_points:
        placed = False
        if point.start is not None:
            for chapter in summary.chapters:
                end = chapter.end if chapter.end is not None else float("inf")
                if chapter.start <= point.start < end:
                    chapter_points.setdefault(chapter.index, []).append(point.text)
                    placed = True
                    break
        if not placed:
            unassigned.append(point.text)

    for chapter in summary.chapters:
        lines.append(f"- {chapter.title}（{format_clock(chapter.start)}）")
        for text in chapter_points.get(chapter.index, []):
            lines.append(f"  - {text}")
    if unassigned:
        lines += ["", "## 补充要点", ""]
        lines += [f"- {text}" for text in unassigned]
    lines.append("")
    return "\n".join(lines)


def render_ads_record(
    meta: VideoMeta,
    ads: AdsResult,
    *,
    refine: RefineResult | None = None,
    asr: AsrResult | None = None,
) -> str:
    """「省略广告」记录：被省略段落的文本与时间戳（供核对与恢复）。"""
    texts = {seg.idx: seg.refined_text for seg in refine.segments} if refine else {}
    fallback = {seg.idx: seg.text for seg in asr.segments} if asr else {}
    total = sum(item.duration_sec for item in ads.ranges)
    lines: list[str] = [
        "VIDAR 广告省略记录",
        "==============================",
        f"视频：{meta.title}（{meta.bvid}）",
        f"来源：{meta.url}",
        f"生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"共省略 {len(ads.ranges)} 段广告，合计 {format_clock(total)}。",
        "以下内容已从原始稿 / 精修稿及各成品中省略（仅本文件保留）。",
        "",
    ]
    for number, item in enumerate(ads.ranges, start=1):
        reason = f"（{item.reason}）" if item.reason else ""
        lines.append(
            f"【广告 {number}】{format_clock(item.start)} – {format_clock(item.end)}{reason}"
        )
        for idx in range(item.start_idx, item.end_idx + 1):
            text = (texts.get(idx) or fallback.get(idx) or "").strip()
            if text:
                lines.append(f"  {text}")
        lines.append("")
    return "\n".join(lines)


def render_srt(
    refine: RefineResult | None,
    asr: AsrResult,
    *,
    translation: TranslateResult | None = None,
    bilingual: bool = False,
) -> str:
    """以精修稿（无则原始稿）生成 SRT；有译文时用译文（双语时译文在上、原文在下）。"""
    mapping = _translation_map(translation, translation is not None)
    if refine is not None and refine.segments:
        source = [
            (seg.start, seg.end, seg.refined_text, mapping.get(seg.idx, ""))
            for seg in refine.segments
        ]
    else:
        source = [(seg.start, seg.end, seg.text, "") for seg in asr.segments]
    lines: list[str] = []
    for number, (start, end, text, translated) in enumerate(source, start=1):
        body = (f"{translated}\n{text}" if bilingual else translated) if translated else text
        lines += [
            str(number),
            f"{format_srt_time(start)} --> {format_srt_time(end)}",
            body,
            "",
        ]
    return "\n".join(lines)

"""提示词模板（M2 使用）。

统一原则：
1. 只输出 JSON，不输出解释；
2. 精修「不增不减」，摘要「只依据给定内容」；
3. LLM 全程不接触时间数字，时间锚点用「段号」表示，由本地映射回时间戳。
"""

from __future__ import annotations

REFINE_SYSTEM = "你是一名严谨的中文视频文稿精修编辑。你只做订正，绝不改写内容。"


def refine_prompt(text: str, glossary: str = "") -> str:
    glossary_part = f"\n术语表（必须遵循）：\n{glossary}\n" if glossary.strip() else ""
    return f"""请精修下面这段语音识别文稿。

规则：
1. 只允许：删除无意义口头语（嗯/啊/呃/这个/就是说等填充词）、
   修正同音错别字与识别错误、整理标点断句、统一术语与人名。
2. 不允许：改变原意、增加原文没有的信息、删除有效内容、概括压缩。
3. 数字、英文术语、代码、公式保持原貌（明显识别错误除外）。
4. 保持简体中文。{glossary_part}
输出 JSON：{{"refined": "精修后的文本"}}，不要输出任何解释。

原文：
{text}"""


CHAPTERS_SYSTEM = "你是一名视频内容编辑，擅长把长文稿切分为逻辑清晰的章节。"


def chapters_prompt(paragraphs: str, expected: str = "4-10") -> str:
    return f"""下面是一份视频精修文稿，每段前有编号（如 [12]）。请把它切分为 {expected} 个章节。

要求：
1. 章节按时间顺序，边界落在段与段的交界处，不要切在段中间。
2. 标题用简体中文，不超过 14 个字，能概括该段内容（避免"第一部分"这类空话）。
3. 输出 JSON：{{"chapters": [{{"title": "章节标题", "start_index": 起始段号}}]}}
4. 不要输出解释。

文稿：
{paragraphs}"""


MAP_SYSTEM = "你是一名速记编辑，从片段文稿中提炼事实性要点。"


def map_summary_prompt(block: str) -> str:
    return f"""阅读下面的文稿片段（每段前是段号）。提炼 1-3 条要点。

规则：
1. 只写片段中明确出现的内容，保留关键数字、结论、术语。
2. 每条要点 15-60 字，说清楚发生了什么或观点是什么。
3. 输出 JSON：{{"points": [{{"text": "要点", "anchor": 段号}}]}}
4. 不要输出解释。

片段：
{block}"""


REDUCE_SYSTEM = "你是一名内容总结编辑，只依据给定要点工作，绝不添加外部信息。"


def reduce_summary_prompt(points_json: str, chapters_hint: str = "") -> str:
    chapters_part = f"\n{chapters_hint}\n" if chapters_hint.strip() else ""
    return f"""下面是同一视频各片段的要点（JSON，含时间锚点段号）。请归纳总结。

要求：
1. 一句话结论（≤50 字，说清视频的核心价值）。
2. 5-10 条核心要点，按重要度排序，每条 15-60 字，尽量保留数字与术语。
3. 若提供章节信息，为每个章节写 1-2 句小结。
4. 只依据给定内容；不确定或未出现的信息一律省略。
{chapters_part}
输出 JSON：{{"one_liner": "一句话结论",
"key_points": [{{"text": "要点", "anchor": 段号}}],
"chapter_summaries": [{{"chapter_index": 0, "summary": "小结"}}]}}

要点 JSON：
{points_json}"""

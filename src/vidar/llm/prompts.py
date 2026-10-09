"""提示词模板（M2 使用）。

统一原则：
1. 只输出 JSON，不输出解释；
2. 精修「不增不减」，摘要「只依据给定内容」；
3. LLM 全程不接触时间数字，时间锚点用「段号」表示，由本地映射回时间戳。
"""

from __future__ import annotations

REFINE_SYSTEM = "你是一名严谨的视频文稿精修编辑。你只做订正，绝不改写、绝不翻译。"


def refine_prompt(numbered_paragraphs: str, glossary: str = "") -> str:
    """段级精修：逐段独立改写，输出与输入段号 1:1 对应。"""
    glossary_part = f"\n术语表（必须遵循）：\n{glossary}\n" if glossary.strip() else ""
    return f"""请逐段精修下面的语音识别文稿（每段以 [段号] 开头）。

规则：
1. 只允许：删除无意义口头语（嗯/啊/呃/这个/就是说等填充词）、
   修正同音错别字与识别错误、整理标点断句、统一术语与人名。
2. 不允许：改变原意、增加原文没有的信息、删除有效内容、概括压缩、翻译成其他语言。
3. 必须逐段独立处理：不得合并段落、不得拆分段落、段号与顺序保持不变。
4. 保持文稿原有语言（中文文稿输出简体中文，英文/日文等保持原语言）；
   数字、英文术语、代码、公式保持原貌（明显识别错误除外）。{glossary_part}
输出 JSON：{{"refined": [{{"index": 段号, "text": "精修后的段落"}}, ...]}}，
段号与输入一一对应且完整，不要输出任何解释。

文稿：
{numbered_paragraphs}"""


CHAPTERS_SYSTEM = "你是一名视频内容编辑，擅长把长文稿切分为逻辑清晰的章节。"


def chapters_prompt(paragraphs: str, expected: str = "4-10") -> str:
    return f"""下面是一份视频精修文稿，每段前有编号（如 [12]）。请把它切分为 {expected} 个章节。

要求：
1. 章节按时间顺序，边界落在段与段的交界处，不要切在段中间。
2. 标题语言与文稿一致（中文文稿用中文，英文文稿用英文），不超过 14 个字，
   能概括该段内容（避免"第一部分"这类空话）。
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
2. 5-10 条核心要点，按重要度排序，每条 15-60 字，尽量保留数字与术语；
   每条必须携带 anchor（从给定要点中原样选取段号，不得编造段号）。
3. 若提供章节信息，为每个章节输出：
   - summary：1-2 句小结，直接陈述该章讲了什么（不要复述标题、不要"章节标题为…"这类话术）；
   - quote：从该章内容中挑 1 句金句（保留原话风格，≤40 字；无合适内容则留空字符串）；
   - figures：值得配图的时刻列表。判断标准：只要该章存在可视内容
     （演示、实物、操作、图表、场景画面），通常应给出 1-2 个；
     完全纯口播（无演示、无画面变化）时才留空数组。
     每项含 anchor（段号）与 caption（一句话图注，如"三马串联示意"，≤20 字）。
4. 只依据给定内容；不确定或未出现的信息一律省略。
5. 输出语言与给定要点一致（中文要点用中文输出，英文要点用英文输出）。
{chapters_part}
输出 JSON：{{"one_liner": "一句话结论",
"key_points": [{{"text": "要点", "anchor": 段号}}],
"chapters": [{{"chapter_index": 0, "summary": "小结", "quote": "金句",
"figures": [{{"anchor": 段号, "caption": "图注"}}]}}]}}

要点 JSON：
{points_json}"""


TRANSLATE_SYSTEM = "你是一名专业字幕翻译，忠实、通顺、口语自然；只输出 JSON，不输出解释。"


def translate_prompt(
    numbered_paragraphs: str,
    source_language: str,
    target_language: str,
    *,
    strict: bool = False,
) -> str:
    """逐段翻译：段号与输入 1:1 对应，输出 JSON。"""
    strict_part = "\n5. 必须覆盖每一个段号，一个都不能少。" if strict else ""
    return f"""把下面的{source_language}文稿逐段翻译成{target_language}（每段以 [段号] 开头）。

规则：
1. 逐段独立翻译：不合并段落、不拆分段落，段号与顺序保持不变。
2. 忠实原意；术语、人名、数字保持准确（专有名词可保留原文，必要时括注）。
3. 意译优先于直译，目标语言表达自然、保留讲话风格与语气；不添加原文没有的信息。
4. 只输出 JSON，不要任何解释或标注。{strict_part}
输出 JSON：{{"segments": [{{"idx": 段号, "text": "译文"}}, ...]}}

文稿：
{numbered_paragraphs}"""

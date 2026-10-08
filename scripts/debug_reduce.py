"""调试：直接调用 reduce 提示词，查看 LLM 是否返回 figures。"""

from __future__ import annotations

import json
import sys

from vidar.config import load_settings
from vidar.llm.client import LlmClient
from vidar.llm.prompts import REDUCE_SYSTEM, reduce_summary_prompt
from vidar.models import RefineResult
from vidar.pipeline.context import RunContext


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ctx = RunContext(load_settings(None), "BV1MSab65EHG")
    refine = RefineResult.model_validate_json(
        ctx.artifact("refine.json").read_text(encoding="utf-8")
    )
    points = [
        {"text": seg.refined_text[:60], "anchor": seg.idx} for seg in refine.segments
    ]
    chapters_hint = "章节信息（chapter_index / title）：\n" + json.dumps(
        [
            {"chapter_index": 0, "title": "三匹马的功能介绍"},
            {"chapter_index": 1, "title": "串绳牵引三匹马"},
            {"chapter_index": 2, "title": "行军分工与意外骨折"},
            {"chapter_index": 3, "title": "到战场换甲骑战马"},
            {"chapter_index": 4, "title": "副马驮马的备用作用"},
        ],
        ensure_ascii=False,
    )
    client = LlmClient(ctx.settings.llm)
    prompt = reduce_summary_prompt(json.dumps(points, ensure_ascii=False), chapters_hint)
    payload = client.complete_json(
        prompt, system=REDUCE_SYSTEM, model=ctx.settings.llm.model_for("summary")
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2)[:2500])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

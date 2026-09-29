"""LLM 连通性测试：发极短请求验证 Base / Key / 模型 / JSON 模式。

用法：uv run python scripts/llm_ping.py
"""

from __future__ import annotations

import sys

from vidar.config import load_settings
from vidar.llm.client import LlmClient


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    settings = load_settings(None)
    llm = settings.llm
    print(f"api_base : {llm.api_base}")
    print(f"model    : {llm.model}")
    print(f"api_key  : {'已配置' if llm.api_key else '未配置'}")
    if not llm.configured:
        print("✗ LLM 未配置（需要 api_base + api_key）")
        return 1

    client = LlmClient(llm)
    text = client.complete("只回复两个字：成功", system="你是测试助手")
    print(f"普通调用 : {text.strip()[:60]}")

    payload = client.complete_json(
        '请输出 JSON：{"ok": true, "msg": "你好"}', system="你只输出 JSON，不要解释"
    )
    print(f"JSON 调用: {payload}")
    print(f"tokens   : in={client.usage.prompt_tokens} out={client.usage.completion_tokens}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

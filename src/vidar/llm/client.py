"""OpenAI 兼容 LLM 客户端：重试、JSON 解析、token 记账。

兼容 OpenCode Go/Zen：自动附加 `User-Agent: vidar/<version>` 与稳定会话头
`x-opencode-session`（按其接入规范，便于路由与提示词缓存）。
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from uuid import uuid4

from .. import __version__
from ..config import LlmSettings
from ..errors import DependencyError, LlmError
from ..logging_setup import get_logger

log = get_logger("llm")


@dataclass
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    calls: int = 0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def add(self, prompt_tokens: int, completion_tokens: int) -> None:
        self.prompt_tokens += prompt_tokens
        self.completion_tokens += completion_tokens
        self.calls += 1


def parse_json_object(text: str) -> dict:
    """从模型输出中解析 JSON 对象：容忍 ```json 围栏与前后杂讯。"""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[A-Za-z0-9_-]*\s*", "", cleaned)
        cleaned = re.sub(r"\s*```\s*$", "", cleaned)
    for candidate in (cleaned, _slice_json(cleaned)):
        if not candidate:
            continue
        try:
            data = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            return data
    raise LlmError("LLM 返回内容不是合法 JSON 对象", hint=cleaned[:200])


def _slice_json(text: str) -> str | None:
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end <= start:
        return None
    return text[start : end + 1]


class LlmClient:
    def __init__(self, settings: LlmSettings) -> None:
        if not settings.configured:
            raise LlmError(
                "LLM 未配置：缺少 api_base 或 api_key",
                hint="设置环境变量 VIDAR_LLM_API_KEY / VIDAR_LLM_API_BASE，"
                "或参考 config.example.toml 配置 [llm]",
            )
        try:
            from openai import OpenAI
        except ImportError as exc:  # pragma: no cover
            raise DependencyError("缺少依赖 openai", hint="先执行 uv sync") from exc

        self.settings = settings
        self.usage = Usage()
        headers = {"User-Agent": f"vidar/{__version__}"}
        if "opencode.ai" in settings.api_base:
            # OpenCode Go/Zen 接入规范：稳定会话 ID，利于路由与提示词缓存
            headers["x-opencode-session"] = uuid4().hex
        self._client = OpenAI(
            api_key=settings.api_key,
            base_url=settings.api_base,
            timeout=settings.timeout_sec,
            max_retries=0,  # 由本类统一控制重试
            default_headers=headers,
        )

    # ------------------------------------------------------------------ #
    def complete(self, prompt: str, *, system: str = "", model: str | None = None) -> str:
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        return self._with_retry(messages, model or self.settings.model)

    def complete_json(
        self, prompt: str, *, system: str = "", model: str | None = None
    ) -> dict:
        return parse_json_object(self.complete(prompt, system=system, model=model))

    # ------------------------------------------------------------------ #
    def _with_retry(self, messages: list[dict[str, str]], model: str) -> str:
        use_json = True
        last_exc: Exception | None = None
        for attempt in range(self.settings.max_retries + 1):
            try:
                return self._call(messages, model, use_json)
            except Exception as exc:  # noqa: BLE001 - SDK 异常类型依服务商而异
                last_exc = exc
                if use_json and "response_format" in str(exc).lower():
                    # 服务商不支持 JSON 模式：降级为普通模式，立即重试
                    use_json = False
                    continue
                if attempt < self.settings.max_retries:
                    wait = 2**attempt
                    log.warning("LLM 调用失败（第 %d 次），%ds 后重试：%s", attempt + 1, wait, exc)
                    time.sleep(wait)
        raise LlmError("LLM 调用失败（已重试）", hint=str(last_exc)[:300])

    def _call(self, messages: list[dict[str, str]], model: str, use_json: bool) -> str:
        kwargs: dict = {
            "model": model,
            "messages": messages,
            "temperature": self.settings.temperature,
        }
        if use_json:
            kwargs["response_format"] = {"type": "json_object"}
        response = self._client.chat.completions.create(**kwargs)
        usage = getattr(response, "usage", None)
        if usage is not None:
            self.usage.add(
                int(getattr(usage, "prompt_tokens", 0) or 0),
                int(getattr(usage, "completion_tokens", 0) or 0),
            )
        content = response.choices[0].message.content or ""
        return content

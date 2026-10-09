"""LLM 服务商预设与连通性测试（接入引导的服务层）。

- `PRESETS`：常见 OpenAI 兼容服务商（含本地 Ollama），带 Key 环境变量与获取链接；
- `detect_preset`：按 api_base 反查当前配置对应哪个预设；
- `test_connection`：一次最小 chat 请求验证 Key/地址/模型，返回人话结论与修复建议；
- `list_models`：best-effort 拉取模型列表（供 GUI 下拉选择）。
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from ..config import LlmSettings


@dataclass(frozen=True)
class ProviderPreset:
    key: str
    label: str
    api_base: str
    model: str
    env_key: str = ""
    key_url: str = ""
    note: str = ""


PRESETS: tuple[ProviderPreset, ...] = (
    ProviderPreset(
        "opencode-go",
        "OpenCode Go（Zen 网关，多模型路由）",
        "https://opencode.ai/zen/go/v1",
        "deepseek-v4.1-flash",
        "OPENCODE_GO_API_KEY",
        "https://opencode.ai",
        "支持 DeepSeek / GLM 等模型；Key 从 OpenCode 控制台获取",
    ),
    ProviderPreset(
        "deepseek",
        "DeepSeek 官方",
        "https://api.deepseek.com/v1",
        "deepseek-chat",
        "DEEPSEEK_API_KEY",
        "https://platform.deepseek.com",
        "性价比高，国内直连稳定",
    ),
    ProviderPreset(
        "siliconflow",
        "硅基流动 SiliconFlow",
        "https://api.siliconflow.cn/v1",
        "deepseek-ai/DeepSeek-V3",
        "SILICONFLOW_API_KEY",
        "https://cloud.siliconflow.cn",
        "国内多模型聚合，有免费额度",
    ),
    ProviderPreset(
        "moonshot",
        "月之暗面 Kimi",
        "https://api.moonshot.cn/v1",
        "moonshot-v1-8k",
        "MOONSHOT_API_KEY",
        "https://platform.moonshot.cn",
        "长文本能力好",
    ),
    ProviderPreset(
        "zhipu",
        "智谱 GLM",
        "https://open.bigmodel.cn/api/paas/v4",
        "glm-4-flash",
        "ZHIPU_API_KEY",
        "https://bigmodel.cn",
        "glm-4-flash 免费额度充足",
    ),
    ProviderPreset(
        "ollama",
        "本地 Ollama（免费 / 离线）",
        "http://127.0.0.1:11434/v1",
        "qwen2.5:7b",
        "",
        "https://ollama.com",
        "无需 Key；先安装 Ollama 并执行 ollama pull qwen2.5:7b",
    ),
)

CUSTOM_HINT = "也可使用任意 OpenAI 兼容服务：配置 [llm].api_base / api_key / model 即可"


def find_preset(key: str) -> ProviderPreset | None:
    normalized = (key or "").strip().lower()
    for preset in PRESETS:
        if preset.key == normalized:
            return preset
    return None


def detect_preset(api_base: str) -> ProviderPreset | None:
    """按 api_base 反查预设（用于展示「当前使用：xxx」）。"""
    normalized = (api_base or "").strip().rstrip("/").lower()
    if not normalized:
        return None
    for preset in PRESETS:
        if preset.api_base.rstrip("/").lower() == normalized:
            return preset
    return None


@dataclass
class ConnectionReport:
    ok: bool
    message: str
    hint: str = ""
    latency_ms: int = 0


def _hint_for_error(message: str) -> str:
    """把常见报错翻译成可执行的修复建议。"""
    text = message.lower()
    if any(
        token in text
        for token in ("401", "invalid api key", "authentication", "unauthorized")
    ):
        return "API Key 无效或过期：检查 [llm].api_key，或重新生成 Key"
    if "429" in text or "rate limit" in text or "quota" in text or "insufficient" in text:
        return "请求过于频繁或额度不足：稍后重试，或检查账户余额"
    if "404" in text:
        return "地址或模型名不对：api_base 通常要以 /v1 结尾；确认模型名存在"
    if "timeout" in text or "timed out" in text:
        return "网络超时：检查网络/代理；国内访问部分服务商需要直连"
    if "connection" in text or "refused" in text or "unreachable" in text:
        return "连不上服务：检查网络；本地 Ollama 需先启动 ollama serve"
    if "model" in text and "not found" in text:
        return "模型名不存在：换一个模型名（见 vidar llm presets）"
    return ""


def _is_local(api_base: str) -> bool:
    text = (api_base or "").lower()
    return "127.0.0.1" in text or "localhost" in text


def test_connection(settings: LlmSettings, *, model: str | None = None) -> ConnectionReport:
    """最小 chat 请求验证可用性（1 token，成本可忽略）。"""
    target_model = model or settings.model
    if not settings.api_base:
        return ConnectionReport(False, "未配置 api_base", "先执行：vidar llm setup <预设>")
    if not settings.api_key and not _is_local(settings.api_base):
        return ConnectionReport(
            False,
            "未配置 api_key",
            "设置环境变量（如 OPENCODE_GO_API_KEY）或在 config.toml 的 [llm] 填写",
        )
    try:
        from openai import OpenAI
    except ImportError:  # pragma: no cover
        return ConnectionReport(False, "缺少依赖 openai", "先执行 uv sync")

    started = time.perf_counter()
    from .client import request_headers

    try:
        client = OpenAI(
            api_key=settings.api_key or "EMPTY",
            base_url=settings.api_base,
            timeout=20,
            max_retries=0,
            default_headers=request_headers(settings.api_base),
        )
        client.chat.completions.create(
            model=target_model,
            messages=[{"role": "user", "content": "ping"}],
            max_tokens=1,
            temperature=0,
        )
    except Exception as exc:  # noqa: BLE001 - SDK 异常类型依服务商而异
        message = str(exc)
        return ConnectionReport(False, f"连接失败：{message[:160]}", _hint_for_error(message))
    latency = int((time.perf_counter() - started) * 1000)
    return ConnectionReport(True, f"连接成功（{latency} ms）：{target_model} @ {settings.api_base}")


def list_models(settings: LlmSettings, *, timeout: float = 15.0) -> list[str]:
    """best-effort 拉取 /models（部分服务商不支持时返回空列表）。"""
    if not settings.api_base:
        return []
    try:
        import httpx

        from .client import request_headers

        headers = request_headers(settings.api_base)
        headers["Authorization"] = f"Bearer {settings.api_key or 'EMPTY'}"
        with httpx.Client(timeout=timeout, follow_redirects=True, headers=headers) as client:
            response = client.get(f"{settings.api_base.rstrip('/')}/models")
            response.raise_for_status()
            data = response.json().get("data") or []
            return sorted(
                str(item["id"])
                for item in data
                if isinstance(item, dict) and item.get("id")
            )
    except Exception:  # noqa: BLE001 - 探测失败不影响主流程
        return []


__all__ = [
    "CUSTOM_HINT",
    "PRESETS",
    "ConnectionReport",
    "ProviderPreset",
    "detect_preset",
    "find_preset",
    "list_models",
    "test_connection",
]

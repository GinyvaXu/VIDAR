"""配置系统：默认值 < config.toml < 环境变量 < CLI 参数。

配置文件查找顺序（第一个存在的生效）：
    1. --config 显式指定（不存在则报错）
    2. 当前工作目录 ./config.toml
    3. 用户配置目录（Windows: %APPDATA%\\vidar\\config.toml）

环境变量：
    - 嵌套写法：VIDAR_ASR__MODEL=medium  （双下划线表示层级）
    - 常用扁平写法：VIDAR_LLM_API_KEY / VIDAR_LLM_API_BASE / VIDAR_LLM_MODEL
    - 兼容官方 SDK 命名：OPENAI_API_KEY / OPENAI_BASE_URL
"""

from __future__ import annotations

import os
import sys
import tomllib
from pathlib import Path
from typing import Any

import tomli_w
from platformdirs import user_config_dir, user_data_dir
from pydantic import BaseModel, Field

from .errors import ConfigError

APP_NAME = "vidar"
ENV_PREFIX = "VIDAR_"
LEGACY_ENV_PREFIX = "BILIKING_"  # 更名前的环境变量前缀，继续兼容

# 扁平环境变量 → 配置路径（便于常用项少打字）
_FLAT_ENV_MAP: dict[str, tuple[str, str]] = {
    "VIDAR_LLM_API_KEY": ("llm", "api_key"),
    "VIDAR_LLM_API_BASE": ("llm", "api_base"),
    "VIDAR_LLM_MODEL": ("llm", "model"),
    "VIDAR_ASR_ENGINE": ("asr", "engine"),
    "VIDAR_ASR_MODEL": ("asr", "model"),
    "VIDAR_ASR_DEVICE": ("asr", "device"),
    "VIDAR_ASR_COMPUTE_TYPE": ("asr", "compute_type"),
    "VIDAR_ASR_LANGUAGE": ("asr", "language"),
    "VIDAR_OUTPUT_DIR": ("paths", "output_dir"),
    "VIDAR_WORK_DIR": ("paths", "work_dir"),
    "VIDAR_LOGS_DIR": ("paths", "logs_dir"),
    "VIDAR_COOKIES": ("paths", "cookies"),
    "VIDAR_FFMPEG": ("paths", "ffmpeg"),
    # 兼容 OpenAI SDK 生态
    "OPENAI_API_KEY": ("llm", "api_key"),
    "OPENAI_BASE_URL": ("llm", "api_base"),
    # OpenCode Go / Zen
    "OPENCODE_GO_API_KEY": ("llm", "api_key"),
    "OPENCODE_GO_API_BASE": ("llm", "api_base"),
    "OPENCODE_API_KEY": ("llm", "api_key"),
}

# 旧名兼容：BILIKING_XXX 与 VIDAR_XXX 等效
for _legacy_name, _legacy_target in list(_FLAT_ENV_MAP.items()):
    if _legacy_name.startswith(ENV_PREFIX):
        _FLAT_ENV_MAP[_legacy_name.replace(ENV_PREFIX, LEGACY_ENV_PREFIX, 1)] = _legacy_target


def _flat_env_priority(name: str) -> tuple[int, str]:
    """同名目标的环境变量优先级：VIDAR_ > BILIKING_ > 官方 SDK 命名。"""
    if name.startswith(ENV_PREFIX):
        group = 0
    elif name.startswith(LEGACY_ENV_PREFIX):
        group = 1
    else:
        group = 2
    return (group, name)


# LLM Key 环境变量（按优先级）：用于「环境变量已提供 Key 则不落盘」的保护
_LLM_KEY_ENV_NAMES: tuple[str, ...] = (
    "VIDAR_LLM_API_KEY",
    "BILIKING_LLM_API_KEY",
    "OPENCODE_GO_API_KEY",
    "OPENCODE_API_KEY",
    "OPENAI_API_KEY",
)


def llm_key_from_env() -> str:
    """当前环境变量提供的 LLM Key（取第一个非空）。"""
    for name in _LLM_KEY_ENV_NAMES:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return ""


class PathsSettings(BaseModel):
    output_dir: Path = Path("output")
    work_dir: Path = Path("work")
    logs_dir: Path = Path("logs")
    cookies: Path | None = None
    ffmpeg: str | None = None
    ffprobe: str | None = None


class AsrSettings(BaseModel):
    engine: str = "faster-whisper"
    model: str = "large-v3"
    device: str = "auto"  # auto | cuda | cpu
    compute_type: str = "auto"  # auto | float16 | int8_float16 | int8
    language: str = "auto"  # auto 自动检测 | zh | en | ja | ko …
    vad: bool = True
    beam_size: int = 5
    filter_hallucinations: bool = True
    initial_prompt: str = "以下是普通话视频转写，请输出简体中文，保留标点。"
    hf_endpoint: str = "https://hf-mirror.com"  # HuggingFace 镜像；置空使用官方源


class UiSettings(BaseModel):
    theme: str = "auto"  # auto 跟随系统 | light 浅色 | dark 深色
    llm_wizard_done: bool = False  # LLM 接入向导是否已完成（未配置 LLM 时首启引导）


class ParagraphSettings(BaseModel):
    """语段编辑：LLM 把零散句子组织成自然段（首行缩进两格由排版层实现）。"""

    enabled: bool = True
    min_chars: int = 60  # 段落下限（过短自动并入相邻段）
    max_chars: int = 260  # 段落上限（过长在句边界自动拆分）


class TranslateSettings(BaseModel):
    """双语翻译：检测到非目标语言时，逐段翻译为对照译文。"""

    enabled: bool = True
    target_language: str = "zh"  # 目标语言（zh 中文 | en 英文 | …）
    max_chars: int = 2500  # 单次请求的源文本字符上限（含段号）
    skip_languages: bool = True  # 源语言命中目标语言时自动跳过


class AdsSettings(BaseModel):
    """广告识别：LLM 标记真广告（赞助/带货/商务植入）并从成品省略。

    被省略内容另存「省略广告版-*.txt」记录；原始/精修逐字稿同样省略。
    """

    enabled: bool = True


class LlmSettings(BaseModel):
    api_base: str = ""
    api_key: str = ""
    model: str = "deepseek-chat"
    refine_model: str = ""
    summary_model: str = ""
    translate_model: str = ""
    temperature: float = 0.2
    timeout_sec: int = 120
    max_retries: int = 3
    chunk_chars: int = 3500

    @property
    def configured(self) -> bool:
        return bool(self.api_key and self.api_base)

    def model_for(self, step: str) -> str:
        """按步骤取模型：refine_model / summary_model / translate_model 覆盖默认 model。"""
        if step == "refine" and self.refine_model:
            return self.refine_model
        if step == "summary" and self.summary_model:
            return self.summary_model
        if step == "translate" and self.translate_model:
            return self.translate_model
        return self.model


class ExportSettings(BaseModel):
    srt: bool = False
    keep_media: bool = False
    keep_audio: bool = False
    screenshots: bool = True  # 关键帧截图（供 docx/pdf 使用）
    video_max_height: int = 720  # 抽帧用视频清晰度
    max_images: int = 40  # 章节配图软上限（全片）
    min_gap_sec: float = 15.0  # 相邻章节配图最小间隔
    pdf: bool = False  # 导出 PDF（可选）
    pdf_style: str = "newspaper"  # newspaper | paper
    pdf_mode: str = "full"  # full | brief
    pdf_scale: str = "standard"  # 字号档位：compact 小 | standard 标准 | large 大（或数字 0.6–1.5）
    point_figures: str = "all"  # 报刊要点截图密度：all | sparse 隔一取一 | off
    point_thumb_mm: float = 28.0  # 报刊「本期看点」缩略图宽度（mm）
    bilingual: bool = True  # 有译文时双语排版（译文正文 + 原文小字）
    docx: bool = False  # 导出 Word（可选）
    epub: bool = False  # 导出 EPUB 电子书（微信读书 / 墨水屏）


class Settings(BaseModel):
    paths: PathsSettings = Field(default_factory=PathsSettings)
    asr: AsrSettings = Field(default_factory=AsrSettings)
    llm: LlmSettings = Field(default_factory=LlmSettings)
    translate: TranslateSettings = Field(default_factory=TranslateSettings)
    ads: AdsSettings = Field(default_factory=AdsSettings)
    paragraph: ParagraphSettings = Field(default_factory=ParagraphSettings)
    export: ExportSettings = Field(default_factory=ExportSettings)
    ui: UiSettings = Field(default_factory=UiSettings)

    # 运行期附加信息
    config_path: Path | None = None


# --------------------------------------------------------------------------- #
# 合并与加载
# --------------------------------------------------------------------------- #
def _deep_merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


# 公开别名（GUI / 测试使用）
deep_merge = _deep_merge


def default_config_path() -> Path:
    return Path(user_config_dir(APP_NAME)) / "config.toml"


def shared_data_dir() -> Path:
    """机器级共享数据目录（多版本便携包共用，如 GPU 运行时）。

    注意 appauthor=False：避免 platformdirs 默认拼出 `...\\vidar\\vidar`。
    """
    return Path(user_data_dir(APP_NAME, appauthor=False))


def exe_dir() -> Path | None:
    """PyInstaller 便携包模式：返回 exe 所在目录。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return None


def resolve_config_path(explicit: Path | None) -> Path | None:
    if explicit is not None:
        path = Path(explicit)
        if not path.exists():
            raise ConfigError(f"指定的配置文件不存在：{path}", hint="检查 --config 路径是否正确")
        return path
    candidates = [Path.cwd() / "config.toml"]
    if (folder := exe_dir()) is not None:
        candidates.append(folder / "config.toml")
    candidates.append(default_config_path())
    candidates.append(Path(user_config_dir("biliking")) / "config.toml")  # 旧名目录兼容
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def _env_overrides() -> dict[str, Any]:
    data: dict[str, Any] = {}

    def assign(section: str, key: str, value: str) -> None:
        """同一目标只取第一个（优先级见 _flat_env_priority），避免依赖环境变量遍历顺序。"""
        node = data.setdefault(section, {})
        if key not in node:
            node[key] = value

    # 1) 扁平写法：VIDAR_ 命名优先，其次旧前缀，最后官方 SDK 命名
    flat_keys = sorted(
        (name for name in os.environ if name in _FLAT_ENV_MAP), key=_flat_env_priority
    )
    for raw_key in flat_keys:
        raw_value = os.environ.get(raw_key, "")
        if not raw_value:
            continue
        section, key = _FLAT_ENV_MAP[raw_key]
        assign(section, key, raw_value)
    # 2) 嵌套写法 VIDAR_ASR__MODEL（旧前缀 BILIKING_ 同样支持）
    for raw_key, raw_value in os.environ.items():
        if not raw_value:
            continue
        for prefix in (ENV_PREFIX, LEGACY_ENV_PREFIX):
            if raw_key.startswith(prefix) and "__" in raw_key:
                path = raw_key[len(prefix) :].split("__")
                node = data
                for part in path[:-1]:
                    node = node.setdefault(part.lower(), {})
                target_key = path[-1].lower()
                node.setdefault(target_key, raw_value)
                break
    return data


def load_settings(
    config_path: Path | None = None,
    overrides: dict[str, Any] | None = None,
) -> Settings:
    """按优先级装配配置。overrides 来自 CLI 参数（最高优先级）。"""
    data: dict[str, Any] = {}
    path = resolve_config_path(config_path)
    if path is not None:
        try:
            # utf-8-sig：容忍 Windows 编辑器写入的 BOM（否则 tomllib 解析失败）
            data = _deep_merge(data, tomllib.loads(path.read_text(encoding="utf-8-sig")))
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError(f"配置文件解析失败：{path}", hint=str(exc)) from exc
    data = _deep_merge(data, _env_overrides())
    if overrides:
        data = _deep_merge(data, overrides)

    settings = Settings.model_validate(data)
    settings.config_path = path
    return settings


def mask_secret(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}...{value[-4:]}"


def dump_settings(settings: Settings) -> dict[str, Any]:
    """用于 config show 的安全展示（密钥掩码）。"""
    data = settings.model_dump(mode="json")
    data.pop("config_path", None)
    api_key = data.get("llm", {}).get("api_key", "")
    data["llm"]["api_key"] = mask_secret(api_key)
    return data


def export_settings_dict(settings: Settings, *, protect_env_secrets: bool = True) -> dict[str, Any]:
    """导出完整配置（含真实密钥），用于写回配置文件。

    protect_env_secrets：环境变量提供的 LLM Key 不落盘（继续由环境变量提供），
    只有用户在设置里显式填写了不同的 Key 才会写入——与 CLI `llm setup` 的规则一致。
    """
    data = settings.model_dump(mode="json")
    data.pop("config_path", None)
    env_key = llm_key_from_env()
    if protect_env_secrets and env_key and data.get("llm", {}).get("api_key") == env_key:
        data["llm"]["api_key"] = ""
    return data


CONFIG_TEMPLATE = """\
# VIDAR 配置文件（vidar config init 生成）
# 优先级：CLI 参数 > 环境变量 > 本文件 > 默认值

[paths]
output_dir = "./output"
work_dir = "./work"
logs_dir = "./logs"
# cookies = "C:/path/to/cookies.txt"
# ffmpeg = "C:/tools/ffmpeg/bin/ffmpeg.exe"

[asr]
engine = "faster-whisper"
model = "large-v3"          # large-v3 | large-v3-turbo | medium | small
device = "auto"             # auto | cuda | cpu
compute_type = "auto"       # auto | float16 | int8_float16 | int8
language = "auto"           # auto 自动检测 | zh | en | ja | ko …（外语视频保持 auto）
vad = true
beam_size = 5
filter_hallucinations = true
hf_endpoint = "https://hf-mirror.com"   # 模型下载镜像；置空则用 HuggingFace 官方源

[llm]
api_base = "https://api.deepseek.com/v1"
api_key = ""                # 建议改用环境变量 VIDAR_LLM_API_KEY
model = "deepseek-chat"
# refine_model = ""
# summary_model = ""
temperature = 0.2
timeout_sec = 120
max_retries = 3
chunk_chars = 3500

[translate]
enabled = true              # 检测到非目标语言时自动翻译（源语言=目标语言时跳过）
target_language = "zh"      # 目标语言：zh 中文 | en 英文 | ja 日文 …
max_chars = 2500            # 单次翻译请求的源文本字符上限

[ads]
enabled = true              # LLM 识别真广告（赞助/带货/商务植入）并从成品省略；
                            # 省略内容另存「省略广告版-标题.txt」，原始/精修稿同样省略

[paragraph]
enabled = true              # LLM 把零散句子编成语段（排版更自然）
min_chars = 60              # 段落下限（过短自动并入相邻段）
max_chars = 260             # 段落上限（过长在句边界自动拆分）

[export]
srt = false
keep_media = false
keep_audio = false
screenshots = true            # 关键帧截图（docx/pdf 配图）
video_max_height = 720        # 抽帧用视频清晰度
max_images = 40               # 全片章节配图软上限
min_gap_sec = 15              # 相邻章节配图最小间隔（秒）
pdf = false                   # 导出 PDF（newspaper 报刊风 / paper 论文风）
pdf_style = "newspaper"
pdf_mode = "full"             # full 全文 | brief 精简
pdf_scale = "standard"        # 字号档位：compact 小 | standard 标准 | large 大（或 0.6–1.5 数字）
point_figures = "all"         # 报刊要点截图密度：all 全部 | sparse 隔一取一 | off 关闭
point_thumb_mm = 28           # 报刊「本期看点」缩略图宽度（mm）
bilingual = true              # 有译文时双语排版（译文正文 + 原文小字）
docx = false                  # 导出 Word（可再编辑）
epub = false                  # 导出 EPUB 电子书（微信读书 / 墨水屏）

[ui]
theme = "auto"                # auto 跟随系统 | light 浅色 | dark 深色
# llm_wizard_done = false     # LLM 接入向导是否已完成（GUI 首启引导）
"""


def write_default_config(path: Path, *, force: bool = False) -> Path:
    if path.exists() and not force:
        raise ConfigError(
            f"配置文件已存在：{path}",
            hint="如需覆盖请加 --force",
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(CONFIG_TEMPLATE, encoding="utf-8")
    return path


def write_config_dict(path: Path, data: dict[str, Any]) -> Path:
    """写回配置（供 config set 等后续功能使用）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(tomli_w.dumps(data), encoding="utf-8")
    return path

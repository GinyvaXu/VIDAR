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
from platformdirs import user_config_dir
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
    language: str = "zh"
    vad: bool = True
    beam_size: int = 5
    filter_hallucinations: bool = True
    initial_prompt: str = "以下是普通话视频转写，请输出简体中文，保留标点。"
    hf_endpoint: str = "https://hf-mirror.com"  # HuggingFace 镜像；置空使用官方源


class LlmSettings(BaseModel):
    api_base: str = ""
    api_key: str = ""
    model: str = "deepseek-chat"
    refine_model: str = ""
    summary_model: str = ""
    temperature: float = 0.2
    timeout_sec: int = 120
    max_retries: int = 3
    chunk_chars: int = 3500

    @property
    def configured(self) -> bool:
        return bool(self.api_key and self.api_base)

    def model_for(self, step: str) -> str:
        """按步骤取模型：refine_model / summary_model 覆盖默认 model。"""
        if step == "refine" and self.refine_model:
            return self.refine_model
        if step == "summary" and self.summary_model:
            return self.summary_model
        return self.model


class ExportSettings(BaseModel):
    srt: bool = False
    keep_media: bool = False
    keep_audio: bool = False


class Settings(BaseModel):
    paths: PathsSettings = Field(default_factory=PathsSettings)
    asr: AsrSettings = Field(default_factory=AsrSettings)
    llm: LlmSettings = Field(default_factory=LlmSettings)
    export: ExportSettings = Field(default_factory=ExportSettings)

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
    for raw_key, raw_value in os.environ.items():
        if not raw_value:
            continue
        # 1) 扁平映射
        if raw_key in _FLAT_ENV_MAP:
            section, key = _FLAT_ENV_MAP[raw_key]
            data.setdefault(section, {})[key] = raw_value
            continue
        # 2) 嵌套写法 VIDAR_ASR__MODEL（旧前缀 BILIKING_ 同样支持）
        for prefix in (ENV_PREFIX, LEGACY_ENV_PREFIX):
            if raw_key.startswith(prefix) and "__" in raw_key:
                path = raw_key[len(prefix) :].split("__")
                node = data
                for part in path[:-1]:
                    node = node.setdefault(part.lower(), {})
                node[path[-1].lower()] = raw_value
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
            data = _deep_merge(data, tomllib.loads(path.read_text(encoding="utf-8")))
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


def export_settings_dict(settings: Settings) -> dict[str, Any]:
    """导出完整配置（含真实密钥），用于写回配置文件。"""
    data = settings.model_dump(mode="json")
    data.pop("config_path", None)
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
language = "zh"
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

[export]
srt = false
keep_media = false
keep_audio = false
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

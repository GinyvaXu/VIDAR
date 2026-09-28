"""配置系统测试：优先级、环境变量、掩码、错误处理。"""

from __future__ import annotations

from pathlib import Path

import pytest

from biliking.config import dump_settings, load_settings, mask_secret
from biliking.errors import ConfigError

_ENV_KEYS = [
    "BILIKING_LLM_API_KEY",
    "BILIKING_LLM_API_BASE",
    "BILIKING_LLM_MODEL",
    "BILIKING_ASR__MODEL",
    "BILIKING_ASR_MODEL",
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
]


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in _ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


def test_toml_loading(tmp_path: Path) -> None:
    cfg = tmp_path / "config.toml"
    cfg.write_text('[asr]\nmodel = "medium"\n[llm]\napi_key = "sk-toml"\n', encoding="utf-8")
    settings = load_settings(cfg)
    assert settings.asr.model == "medium"
    assert settings.llm.api_key == "sk-toml"
    assert settings.config_path == cfg


def test_env_overrides_toml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = tmp_path / "config.toml"
    cfg.write_text('[asr]\nmodel = "medium"\n', encoding="utf-8")
    monkeypatch.setenv("BILIKING_ASR__MODEL", "small")
    monkeypatch.setenv("BILIKING_LLM_API_KEY", "sk-env")
    settings = load_settings(cfg)
    assert settings.asr.model == "small"
    assert settings.llm.api_key == "sk-env"


def test_cli_overrides_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("BILIKING_ASR__MODEL", "small")
    settings = load_settings(None, {"asr": {"model": "large-v3"}})
    assert settings.asr.model == "large-v3"


def test_openai_compatible_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://example.com/v1")
    settings = load_settings(None)
    assert settings.llm.api_key == "sk-openai"
    assert settings.llm.api_base == "https://example.com/v1"
    assert settings.llm.configured is True


def test_cwd_config_is_discovered(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.toml").write_text('[asr]\nmodel = "small"\n', encoding="utf-8")
    settings = load_settings(None)
    assert settings.asr.model == "small"


def test_explicit_missing_config_raises(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        load_settings(tmp_path / "nope.toml")


def test_mask_secret() -> None:
    assert mask_secret("") == ""
    assert mask_secret("short") == "*****"
    hidden = mask_secret("sk-1234567890abcdef")
    assert hidden.startswith("sk-1")
    assert "..." in hidden
    assert hidden.endswith("cdef")


def test_dump_settings_masks_key(tmp_path: Path) -> None:
    cfg = tmp_path / "config.toml"
    cfg.write_text('[llm]\napi_key = "sk-1234567890abcdef"\n', encoding="utf-8")
    settings = load_settings(cfg)
    data = dump_settings(settings)
    assert data["llm"]["api_key"] == "sk-1...cdef"
    assert "config_path" not in data

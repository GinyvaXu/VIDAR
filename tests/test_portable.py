"""版本号与便携包路径解析测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from vidar import __version__
from vidar.asr import resolve_model_reference


def test_version_matches_version_file() -> None:
    version_file = Path(__file__).resolve().parent.parent / "VERSION"
    if not version_file.exists():
        pytest.skip("非源码仓库环境，跳过 VERSION 文件比对")
    assert version_file.read_text(encoding="utf-8").strip() == __version__


def test_model_reference_fallback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    target = tmp_path / "models" / "faster-whisper-large-v3"
    target.mkdir(parents=True)
    assert resolve_model_reference("faster-whisper-large-v3") == str(target)
    assert resolve_model_reference("no-such-model") == "no-such-model"


def test_model_reference_prefers_existing_path(tmp_path: Path) -> None:
    model_dir = tmp_path / "my-model"
    model_dir.mkdir()
    assert resolve_model_reference(str(model_dir)) == str(model_dir)

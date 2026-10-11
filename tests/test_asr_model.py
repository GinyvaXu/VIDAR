"""ASR 模型引用解析：本地目录 / 相对路径回退（exe 目录、共享目录）/ 清晰报错。"""

from __future__ import annotations

from pathlib import Path

import pytest

from vidar.errors import VidarError


@pytest.fixture(autouse=True)
def _isolate_shared_dir(monkeypatch, tmp_path: Path) -> None:  # noqa: ANN001
    """隔离本机真实的共享模型目录（%LOCALAPPDATA%/vidar/models），保证测试确定性。"""
    from vidar import config

    monkeypatch.setattr(config, "shared_data_dir", lambda: tmp_path / "no-shared")


def test_absolute_directory_resolves(tmp_path: Path) -> None:
    from vidar.asr import resolve_model_reference

    model_dir = tmp_path / "faster-whisper-large-v3"
    model_dir.mkdir()
    assert resolve_model_reference(str(model_dir)) == str(model_dir.resolve())


def test_relative_models_dir_resolves(monkeypatch, tmp_path: Path) -> None:  # noqa: ANN001
    from vidar.asr import resolve_model_reference

    monkeypatch.chdir(tmp_path)
    model_dir = tmp_path / "models" / "faster-whisper-large-v3"
    model_dir.mkdir(parents=True)
    assert resolve_model_reference("./models/faster-whisper-large-v3") == str(
        model_dir.resolve()
    )


def test_falls_back_to_exe_dir(monkeypatch, tmp_path: Path) -> None:  # noqa: ANN001
    from vidar import asr

    package = tmp_path / "pkg"
    (package / "models" / "faster-whisper-large-v3").mkdir(parents=True)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    monkeypatch.setattr(asr, "exe_dir", lambda: package)
    assert asr.resolve_model_reference("./models/faster-whisper-large-v3") == str(
        (package / "models" / "faster-whisper-large-v3").resolve()
    )


def test_falls_back_to_shared_data_dir(monkeypatch, tmp_path: Path) -> None:  # noqa: ANN001
    from vidar import asr, config

    shared = tmp_path / "shared"
    (shared / "models" / "faster-whisper-large-v3").mkdir(parents=True)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    monkeypatch.setattr(asr, "exe_dir", lambda: None)
    monkeypatch.setattr(config, "shared_data_dir", lambda: shared)
    assert asr.resolve_model_reference("./models/faster-whisper-large-v3") == str(
        (shared / "models" / "faster-whisper-large-v3").resolve()
    )


def test_missing_path_raises_with_hint(monkeypatch, tmp_path: Path) -> None:  # noqa: ANN001
    from vidar.asr import resolve_model_reference

    monkeypatch.chdir(tmp_path)
    with pytest.raises(VidarError) as excinfo:
        resolve_model_reference("./models/faster-whisper-large-v3")
    assert "不存在" in excinfo.value.message
    assert "model download" in (excinfo.value.hint or "")


def test_repo_id_passthrough(monkeypatch, tmp_path: Path) -> None:  # noqa: ANN001
    from vidar.asr import resolve_model_reference

    monkeypatch.chdir(tmp_path)
    assert resolve_model_reference("large-v3") == "large-v3"
    assert (
        resolve_model_reference("Systran/faster-whisper-large-v3")
        == "Systran/faster-whisper-large-v3"
    )

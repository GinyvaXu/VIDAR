"""模型辅助下载/校验的离线逻辑测试（不触网）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from vidar import model_store
from vidar.model_store import (
    check_model,
    default_model_dir,
    expected_sizes,
    resolve_repo,
)


def test_resolve_repo_aliases() -> None:
    assert resolve_repo("large-v3") == "Systran/faster-whisper-large-v3"
    assert resolve_repo("medium") == "Systran/faster-whisper-medium"
    assert resolve_repo("faster-whisper-small") == "Systran/faster-whisper-small"
    assert resolve_repo("Systran/faster-whisper-large-v3") == "Systran/faster-whisper-large-v3"


def test_default_model_dir(tmp_path: Path) -> None:
    expected = tmp_path / "models" / "faster-whisper-large-v3"
    assert default_model_dir("large-v3", tmp_path) == expected


def test_expected_sizes_offline_fallback() -> None:
    sizes = expected_sizes("Systran/faster-whisper-large-v3")  # endpoint=None → 内置表
    assert sizes is not None
    assert sizes["model.bin"] > 3_000_000_000


def test_check_model_with_known_sizes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_repo = "Fake/tiny-model"
    monkeypatch.setitem(
        model_store.KNOWN_SIZES,
        fake_repo,
        {
            "model.bin": 4,
            "config.json": 2,
            "tokenizer.json": 1,
            "vocabulary.json": 1,
            "preprocessor_config.json": 1,
        },
    )
    model_dir = tmp_path / "tiny"
    model_dir.mkdir()
    for name, size in model_store.KNOWN_SIZES[fake_repo].items():
        (model_dir / name).write_bytes(b"x" * size)

    results = check_model(fake_repo, model_dir)
    assert all(item.ok for item in results)

    (model_dir / "model.bin").write_bytes(b"xx")  # 损坏
    results = check_model(fake_repo, model_dir)
    broken = [item.name for item in results if not item.ok]
    assert broken == ["model.bin"]

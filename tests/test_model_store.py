"""模型辅助下载/校验的离线逻辑测试（不触网）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from vidar import model_store
from vidar.errors import VidarError
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
    # base 参数语义 = 模型根目录（models/ 本身）；默认用 models_root()
    expected = tmp_path / "faster-whisper-large-v3"
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


def test_required_files_includes_feature_config_only_when_repo_has_it() -> None:
    with_pre = {
        "model.bin": 1,
        "config.json": 1,
        "tokenizer.json": 1,
        "vocabulary.json": 1,
        "preprocessor_config.json": 1,
    }
    without_pre = {
        "model.bin": 1,
        "config.json": 1,
        "tokenizer.json": 1,
        "vocabulary.txt": 1,
    }
    assert "preprocessor_config.json" in model_store.required_files(with_pre)
    assert "vocabulary.json" in model_store.required_files(with_pre)
    assert model_store.required_files(without_pre) == [
        "model.bin",
        "config.json",
        "tokenizer.json",
        "vocabulary.txt",
    ]


def test_check_model_flags_missing_preprocessor(tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
    fake_repo = "Fake/large-v3-demo"
    sizes = {
        "model.bin": 4,
        "config.json": 2,
        "tokenizer.json": 1,
        "vocabulary.json": 1,
        "preprocessor_config.json": 1,
    }
    monkeypatch.setitem(model_store.KNOWN_SIZES, fake_repo, sizes)
    model_dir = tmp_path / "demo"
    model_dir.mkdir()
    for name, size in sizes.items():
        if name != "preprocessor_config.json":
            (model_dir / name).write_bytes(b"x" * size)

    results = check_model(fake_repo, model_dir)
    broken = [item.name for item in results if not item.ok]
    assert broken == ["preprocessor_config.json"]


def test_repair_model_writes_128_mels_and_is_idempotent(tmp_path: Path) -> None:
    assert model_store.repair_model("large-v3", tmp_path) is True
    config = json.loads((tmp_path / "preprocessor_config.json").read_text(encoding="utf-8"))
    assert config["feature_size"] == 128

    assert model_store.repair_model("large-v3", tmp_path) is False

    (tmp_path / "preprocessor_config.json").write_text(
        json.dumps({"feature_size": 80}), encoding="utf-8"
    )
    assert model_store.repair_model("large-v3", tmp_path) is True
    config = json.loads((tmp_path / "preprocessor_config.json").read_text(encoding="utf-8"))
    assert config["feature_size"] == 128


def test_repair_model_rejects_unknown_repo(tmp_path: Path) -> None:
    with pytest.raises(VidarError):
        model_store.repair_model("someone/custom-weights", tmp_path)


def test_find_model_dir_by_alias_and_repo(tmp_path: Path) -> None:
    model_dir = tmp_path / "models" / "faster-whisper-large-v3"
    model_dir.mkdir(parents=True)
    assert model_store.find_model_dir("large-v3", base=tmp_path) == model_dir.resolve()
    assert (
        model_store.find_model_dir("Systran/faster-whisper-large-v3", base=tmp_path)
        == model_dir.resolve()
    )
    assert model_store.find_model_dir("medium", base=tmp_path) is None


def test_repair_then_check_accepts_semantic_preprocessor(
    tmp_path: Path, monkeypatch
) -> None:  # noqa: ANN001
    fake_repo = "Fake/large-v3-check"
    sizes = {
        "model.bin": 4,
        "config.json": 2,
        "tokenizer.json": 1,
        "vocabulary.json": 1,
        "preprocessor_config.json": 1,
    }
    monkeypatch.setitem(model_store.KNOWN_SIZES, fake_repo, sizes)
    model_dir = tmp_path / "check"
    model_dir.mkdir()
    for name, size in sizes.items():
        if name != "preprocessor_config.json":
            (model_dir / name).write_bytes(b"x" * size)

    model_store.write_preprocessor_config(model_dir, 128)
    assert (model_dir / "preprocessor_config.json").stat().st_size != 1
    results = model_store.check_model(fake_repo, model_dir)
    assert all(item.ok for item in results)

"""特征维度自愈：preprocessor_config.json 缺失导致 80/128 不匹配时自动校正。"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest

from vidar.asr import align_feature_extractor
from vidar.errors import VidarError


def test_align_feature_extractor_heals_and_persists(tmp_path: Path) -> None:
    pytest.importorskip("faster_whisper")
    from faster_whisper.feature_extractor import FeatureExtractor

    extractor = FeatureExtractor(feature_size=80)
    model = SimpleNamespace(
        model=SimpleNamespace(n_mels=128),
        feature_extractor=extractor,
    )

    fixed = align_feature_extractor(model, str(tmp_path), logger=logging.getLogger("test"))

    assert fixed == 128
    assert model.feature_extractor.mel_filters.shape[0] == 128
    config = json.loads((tmp_path / "preprocessor_config.json").read_text(encoding="utf-8"))
    assert config["feature_size"] == 128
    assert config["n_samples"] == 480000


def test_align_feature_extractor_noop_when_dimensions_match(tmp_path: Path) -> None:
    pytest.importorskip("faster_whisper")
    from faster_whisper.feature_extractor import FeatureExtractor

    model = SimpleNamespace(
        model=SimpleNamespace(n_mels=80),
        feature_extractor=FeatureExtractor(feature_size=80),
    )
    assert align_feature_extractor(model, str(tmp_path)) is None
    assert not (tmp_path / "preprocessor_config.json").exists()


def test_align_feature_extractor_skips_unknown_model(tmp_path: Path) -> None:
    pytest.importorskip("faster_whisper")
    from faster_whisper.feature_extractor import FeatureExtractor

    model = SimpleNamespace(
        model=SimpleNamespace(),
        feature_extractor=FeatureExtractor(feature_size=80),
    )
    assert align_feature_extractor(model, str(tmp_path)) is None


def test_shape_error_is_translated_to_actionable_hint() -> None:
    from vidar.asr import _wrap_gpu_error

    error = _wrap_gpu_error(
        RuntimeError(
            "Invalid input features shape: expected an input with shape (1, 128, 3000), "
            "but got an input with shape (1, 80, 3000) instead"
        )
    )
    assert isinstance(error, VidarError)
    assert "特征维度" in error.message
    assert "model repair" in (error.hint or "")

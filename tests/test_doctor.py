"""doctor 路径基准：相对配置必须锚定 exe 目录，而不是当前工作目录。"""

from __future__ import annotations

from pathlib import Path

from vidar import config, doctor
from vidar.config import Settings


def test_check_dirs_uses_exe_dir(monkeypatch, tmp_path: Path) -> None:  # noqa: ANN001
    monkeypatch.setattr(config, "exe_dir", lambda: tmp_path)
    result = doctor._check_dirs(Settings())
    assert result.status == doctor.STATUS_OK
    assert str(tmp_path / "output") in result.detail
    assert (tmp_path / "work").is_dir()


def test_check_disk_uses_output_dir(monkeypatch, tmp_path: Path) -> None:  # noqa: ANN001
    monkeypatch.setattr(config, "exe_dir", lambda: tmp_path)
    result = doctor._check_disk(Settings())
    assert result.status == doctor.STATUS_OK
    assert "空闲" in result.detail

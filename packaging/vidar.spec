# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置：GUI + CLI 双 exe，共享同一份运行时目录（onedir）。

构建：
    uv run pyinstaller packaging/vidar.spec --noconfirm \
        --distpath dist --workpath build/pyinstaller

产物：dist/VIDAR/
    ├── VIDAR.exe       （图形界面）
    ├── VIDAR-CLI.exe   （命令行）
    └── _internal/              （Python 运行时 + CUDA + Qt + ffmpeg）
"""

import os
import sysconfig

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, copy_metadata

PROJECT_ROOT = os.path.abspath(os.path.join(SPECPATH, os.pardir))  # noqa: F821

# --------------------------------------------------------------------------- #
# 数据文件与二进制
# --------------------------------------------------------------------------- #
datas = [(os.path.join(PROJECT_ROOT, "VERSION"), ".")]

for package in ("vidar", "yt-dlp"):
    try:
        datas += copy_metadata(package)
    except Exception:  # noqa: BLE001 - 元数据缺失不影响运行
        pass

# imageio-ffmpeg 自带静态 ffmpeg（音频转码兜底）
datas += collect_data_files("imageio_ffmpeg")
# faster-whisper 资源文件（Silero VAD onnx 模型等，必需）
datas += collect_data_files("faster_whisper")

# CTranslate2 原生库
binaries = list(collect_dynamic_libs("ctranslate2"))

# CUDA 运行时：pip 安装的 nvidia-* 包（Windows + NVIDIA 显卡）
purelib = sysconfig.get_paths()["purelib"]
for package in ("cublas", "cudnn", "cuda_runtime", "cuda_nvrtc"):
    source = os.path.join(purelib, "nvidia", package, "bin")
    if os.path.isdir(source):
        for filename in os.listdir(source):
            if filename.lower().endswith(".dll"):
                binaries.append((os.path.join(source, filename), f"nvidia/{package}/bin"))

hiddenimports = [
    "nvidia",  # namespace package，静态分析不识别
    "imageio_ffmpeg",
    "yt_dlp",
    "faster_whisper",
    "ctranslate2",
    "onnxruntime",
    "av",
    "tokenizers",
]

excludes = ["tkinter", "matplotlib", "PyQt5", "PyQt6", "IPython"]

# --------------------------------------------------------------------------- #
# 两个入口共享同一运行时目录
# --------------------------------------------------------------------------- #
a_gui = Analysis(  # noqa: F821
    [os.path.join(SPECPATH, "launch_gui.py")],  # noqa: F821
    pathex=[PROJECT_ROOT],
    datas=datas,
    binaries=binaries,
    hiddenimports=hiddenimports,
    excludes=excludes,
    noarchive=False,
)
pyz_gui = PYZ(a_gui.pure)  # noqa: F821
exe_gui = EXE(  # noqa: F821
    pyz_gui,
    a_gui.scripts,
    [],
    exclude_binaries=True,
    name="VIDAR",
    console=False,
    upx=False,
)

a_cli = Analysis(  # noqa: F821
    [os.path.join(SPECPATH, "launch_cli.py")],  # noqa: F821
    pathex=[PROJECT_ROOT],
    datas=datas,
    binaries=binaries,
    hiddenimports=hiddenimports,
    excludes=excludes,
    noarchive=False,
)
pyz_cli = PYZ(a_cli.pure)  # noqa: F821
exe_cli = EXE(  # noqa: F821
    pyz_cli,
    a_cli.scripts,
    [],
    exclude_binaries=True,
    name="VIDAR-CLI",
    console=True,
    upx=False,
)

coll = COLLECT(  # noqa: F821
    exe_gui,
    a_gui.binaries,
    a_gui.datas,
    exe_cli,
    a_cli.binaries,
    a_cli.datas,
    strip=False,
    upx=False,
    name="VIDAR",
)

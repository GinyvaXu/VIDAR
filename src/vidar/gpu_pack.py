"""GPU（CUDA）运行时辅助安装：从 PyPI 镜像下载 nvidia-* wheel 并提取所需 DLL。

默认按"最小可用集"提取（本机实测通过）：
    cublas64_12 + cublasLt64_12
    cudnn64_9 + cudnn_ops + cudnn_cnn + cudnn_graph + cudnn_heuristic + cudnn_engines_precompiled
    cudart64_12
`full=True` 时提取全部 DLL（体积更大，作为疑难兜底）。

安装目标：<目标目录>/nvidia/<包>/bin/ —— 与 asr.cuda_dll_dirs() 的搜索路径一致。
"""

from __future__ import annotations

import shutil
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZipFile

import httpx

from .errors import VidarError
from .utils.download import CancelCheck, ProgressCallback, download_file

MIRRORS: dict[str, str] = {
    "tuna": "https://pypi.tuna.tsinghua.edu.cn",
    "aliyun": "https://mirrors.aliyun.com/pypi",
    "pypi": "https://pypi.org",
}

# 包名 → {wheel 内目录, 最小集文件名}
PACKAGES: dict[str, dict] = {
    "nvidia-cublas-cu12": {
        "dir": "nvidia/cublas",
        "minimal": ["cublas64_12.dll", "cublasLt64_12.dll"],
    },
    "nvidia-cudnn-cu12": {
        "dir": "nvidia/cudnn",
        "minimal": [
            "cudnn64_9.dll",
            "cudnn_ops64_9.dll",
            "cudnn_cnn64_9.dll",
            "cudnn_graph64_9.dll",
            "cudnn_heuristic64_9.dll",
            "cudnn_engines_precompiled64_9.dll",
        ],
    },
    "nvidia-cuda-runtime-cu12": {
        "dir": "nvidia/cuda_runtime",
        "minimal": ["cudart64_12.dll"],
    },
}


def _latest_win_wheel(client: httpx.Client, mirror: str, package: str) -> tuple[str, int]:
    """从 PyPI 镜像的 JSON API 找到最新 Windows wheel 的 URL 与大小。"""
    response = client.get(f"{mirror}/pypi/{package}/json")
    response.raise_for_status()
    for item in response.json().get("urls") or []:
        filename = str(item.get("filename") or "")
        if filename.endswith("win_amd64.whl"):
            return str(item["url"]), int(item.get("size") or 0)
    raise VidarError(
        f"镜像上未找到 {package} 的 Windows wheel",
        hint=f"更换下载源重试（当前：{mirror}）",
    )


def install_gpu_runtime(
    target_dir: Path,
    *,
    mirror: str = "tuna",
    full: bool = False,
    connections: int = 6,
    on_progress: ProgressCallback | None = None,
    should_cancel: CancelCheck | None = None,
) -> list[str]:
    """下载并提取 CUDA 运行时 DLL 到 target_dir/nvidia/...，返回已安装的文件名列表。"""
    base = MIRRORS.get(mirror, mirror)
    target_dir.mkdir(parents=True, exist_ok=True)
    installed: list[str] = []

    with TemporaryDirectory(prefix="vidar-cuda-") as temp_root:
        temp = Path(temp_root)
        with httpx.Client(timeout=httpx.Timeout(30.0, read=120.0), follow_redirects=True) as client:
            for index, (package, spec) in enumerate(PACKAGES.items(), start=1):
                if should_cancel is not None and should_cancel():
                    raise VidarError("已取消（已安装部分保留，可重跑）")
                url, size = _latest_win_wheel(client, base, package)
                wheel_path = temp / f"{package}.whl"
                download_file(
                    url,
                    wheel_path,
                    size,
                    description=f"[{index}/{len(PACKAGES)}] {package}",
                    connections=connections,
                    on_progress=on_progress,
                    should_cancel=should_cancel,
                )

                prefix = f"{spec['dir']}/bin/"
                with ZipFile(wheel_path) as archive:
                    members = [
                        name
                        for name in archive.namelist()
                        if name.startswith(prefix)
                        and name.lower().endswith(".dll")
                        and (full or Path(name).name in spec["minimal"])
                    ]
                    for member in members:
                        destination = target_dir / member
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        with archive.open(member) as source, destination.open("wb") as out:
                            shutil.copyfileobj(source, out, length=4 * 1024 * 1024)
                        installed.append(Path(member).name)
                wheel_path.unlink(missing_ok=True)

    return installed

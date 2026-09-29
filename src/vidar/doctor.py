"""环境自检（vidar doctor）：依赖 / 硬件 / 配置 / 目录 / 网络。"""

from __future__ import annotations

import importlib.metadata
import importlib.util
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from .config import Settings
from .utils.ffmpeg import ffmpeg_version, find_ffmpeg, find_ffprobe

STATUS_OK = "ok"
STATUS_WARN = "warn"
STATUS_FAIL = "fail"

_CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


@dataclass
class CheckResult:
    name: str
    status: str
    detail: str = ""
    hint: str = ""


def _check_python() -> CheckResult:
    version = ".".join(str(part) for part in sys.version_info[:3])
    if sys.version_info >= (3, 11):  # noqa: UP036 - 保留运行期兜底
        return CheckResult("Python", STATUS_OK, version)
    return CheckResult("Python", STATUS_FAIL, version, "需要 Python >= 3.11")


def _check_ffmpeg(settings: Settings) -> CheckResult:
    exe = find_ffmpeg(settings.paths)
    if not exe:
        return CheckResult(
            "ffmpeg",
            STATUS_WARN,
            "未安装（可选）",
            "音频解码已内置（PyAV）；如需 ffprobe 等扩展能力可自行安装并在 config.toml 配置",
        )
    return CheckResult("ffmpeg", STATUS_OK, f"{ffmpeg_version(exe) or '未知版本'}（{exe}）")


def _check_ffprobe(settings: Settings) -> CheckResult:
    exe = find_ffprobe(settings.paths)
    if not exe:
        return CheckResult(
            "ffprobe",
            STATUS_WARN,
            "未找到（非必需）",
            "时长信息取自视频元数据；安装完整 ffmpeg 可增强探测能力",
        )
    return CheckResult("ffprobe", STATUS_OK, exe)


def _check_ytdlp() -> CheckResult:
    if importlib.util.find_spec("yt_dlp") is None:
        return CheckResult("yt-dlp", STATUS_FAIL, "未安装", "uv sync")
    return CheckResult("yt-dlp", STATUS_OK, importlib.metadata.version("yt-dlp"))


def _check_gpu() -> CheckResult:
    exe = shutil.which("nvidia-smi")
    if not exe:
        return CheckResult(
            "NVIDIA GPU",
            STATUS_WARN,
            "未检测到 nvidia-smi",
            "无独显时可改用 CPU（慢）或在线识别接口",
        )
    try:
        result = subprocess.run(
            [exe, "--query-gpu=name,memory.total,driver_version", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            timeout=20,
            creationflags=_CREATE_NO_WINDOW,
        )
    except OSError as exc:
        return CheckResult("NVIDIA GPU", STATUS_WARN, f"nvidia-smi 执行失败：{exc}")
    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if result.returncode != 0 or not lines:
        return CheckResult("NVIDIA GPU", STATUS_WARN, "nvidia-smi 无输出")
    return CheckResult("NVIDIA GPU", STATUS_OK, "；".join(lines))


def _hf_cache_dir() -> Path | None:
    if cache := os.environ.get("HF_HUB_CACHE"):
        return Path(cache)
    if home := os.environ.get("HF_HOME"):
        return Path(home) / "hub"
    default = Path.home() / ".cache" / "huggingface" / "hub"
    return default


def _check_asr(settings: Settings) -> CheckResult:
    if importlib.util.find_spec("faster_whisper") is None:
        return CheckResult(
            "faster-whisper",
            STATUS_WARN,
            "未安装（ASR 不可用）",
            "uv sync --extra asr",
        )
    detail = "已安装"
    cuda_devices = 0
    cuda_runtime_missing = False
    try:
        from .asr import add_cuda_dll_dirs

        add_cuda_dll_dirs()  # Windows：注册 pip 安装（或便携包内置）的 CUDA DLL
        import ctranslate2  # type: ignore[import-not-found]

        cuda_devices = int(ctranslate2.get_cuda_device_count())
        if cuda_devices > 0:
            detail += f"，CUDA 设备 {cuda_devices}"
            from .asr import cuda_dll_dirs

            if not cuda_dll_dirs():
                # 设备计数只查驱动；真正推理还需要 cuBLAS/cuDNN
                cuda_runtime_missing = True
                detail += "；未找到 CUDA 运行时 DLL（GPU 推理会失败）"
        else:
            detail += "，CUDA 不可用（将回退 CPU，速度较慢）"
    except Exception as exc:  # noqa: BLE001 - 不同版本 API 不稳定
        detail += f"，CUDA 检测失败：{exc}"

    cache_dir = _hf_cache_dir()
    model = settings.asr.model
    model_path = Path(model)
    if model_path.is_dir():
        from .model_store import check_model

        file_results = check_model(model_path.name, model_path)
        ok_count = sum(1 for item in file_results if item.ok)
        if ok_count == len(file_results):
            detail += f"；本地模型 OK（{ok_count}/{len(file_results)} 个文件校验通过）"
        else:
            broken = [item.name for item in file_results if not item.ok]
            return CheckResult(
                "faster-whisper",
                STATUS_FAIL,
                f"本地模型不完整：缺/坏 {', '.join(broken)}",
                "运行 vidar model download 修复（支持断点续传），或见 README「模型下载」",
            )
    elif cache_dir is not None and cache_dir.exists():
        if list(cache_dir.glob(f"models--*--faster-whisper-{model}*")):
            detail += f"；模型 {model} 已缓存"
        else:
            detail += f"；模型 {model} 未下载（首次运行自动下载）"

    if cuda_devices <= 0 or cuda_runtime_missing:
        return CheckResult(
            "faster-whisper",
            STATUS_WARN,
            detail,
            "N 卡加速：运行 vidar gpu install 一键安装 GPU 运行时（从 PyPI 镜像下载）",
        )
    return CheckResult("faster-whisper", STATUS_OK, detail)


def _check_llm(settings: Settings, *, net: bool) -> CheckResult:
    llm = settings.llm
    if not llm.configured:
        return CheckResult(
            "LLM",
            STATUS_WARN,
            "未配置 api_base / api_key",
            "设置 VIDAR_LLM_API_KEY 等环境变量，或参考 config.example.toml",
        )
    detail = f"{llm.model} @ {llm.api_base}"
    if not net:
        return CheckResult("LLM", STATUS_OK, detail)
    try:
        import httpx
    except ImportError:  # pragma: no cover
        return CheckResult("LLM", STATUS_OK, detail + "（未安装 httpx，跳过联网检查）")
    try:
        with httpx.Client(timeout=10) as client:
            response = client.get(
                llm.api_base.rstrip("/") + "/models",
                headers={"Authorization": f"Bearer {llm.api_key}"},
            )
    except Exception as exc:  # noqa: BLE001
        return CheckResult("LLM", STATUS_WARN, f"{detail}；连接失败：{exc}", "检查网络或 api_base")
    if response.status_code < 400:
        return CheckResult("LLM", STATUS_OK, f"{detail}；连通性 OK")
    return CheckResult(
        "LLM",
        STATUS_WARN,
        f"{detail}；/models 返回 {response.status_code}",
        "确认 api_base 与 api_key 是否匹配",
    )


def _check_bilibili() -> CheckResult:
    try:
        import httpx
    except ImportError:  # pragma: no cover
        return CheckResult("B站连通性", STATUS_WARN, "未安装 httpx，跳过")
    try:
        # 带浏览器 UA，否则 B 站会返回 412（防爬）
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
            )
        }
        with httpx.Client(timeout=10, follow_redirects=True, headers=headers) as client:
            response = client.get("https://www.bilibili.com")
    except Exception as exc:  # noqa: BLE001
        return CheckResult("B站连通性", STATUS_WARN, f"访问失败：{exc}", "检查网络/代理")
    status = STATUS_OK if response.status_code < 400 else STATUS_WARN
    return CheckResult("B站连通性", status, f"HTTP {response.status_code}")


def _check_dirs(settings: Settings) -> CheckResult:
    targets = [
        settings.paths.output_dir,
        settings.paths.work_dir,
        settings.paths.logs_dir,
    ]
    problems: list[str] = []
    for target in targets:
        path = Path(target)
        if not path.is_absolute():
            path = Path.cwd() / path
        try:
            path.mkdir(parents=True, exist_ok=True)
            probe = path / ".vidar_write_test"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
        except OSError as exc:
            problems.append(f"{path}：{exc}")
    if problems:
        return CheckResult("输出目录", STATUS_FAIL, "；".join(problems), "检查磁盘与权限")
    return CheckResult("输出目录", STATUS_OK, "、".join(str(t) for t in targets))


def _check_disk() -> CheckResult:
    usage = shutil.disk_usage(Path.cwd())
    free_gb = usage.free / (1024**3)
    if free_gb >= 10:
        return CheckResult("磁盘空间", STATUS_OK, f"空闲 {free_gb:.1f} GB")
    return CheckResult(
        "磁盘空间",
        STATUS_WARN,
        f"空闲 {free_gb:.1f} GB",
        "ASR 模型与中间文件需要数 GB 空间",
    )


def run_checks(settings: Settings, *, net: bool = False) -> list[CheckResult]:
    results = [
        _check_python(),
        _check_ffmpeg(settings),
        _check_ffprobe(settings),
        _check_ytdlp(),
        _check_gpu(),
        _check_asr(settings),
        _check_llm(settings, net=net),
        _check_dirs(settings),
        _check_disk(),
    ]
    if net:
        results.append(_check_bilibili())
    return results

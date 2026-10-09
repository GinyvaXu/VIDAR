"""ASR 模型辅助下载与校验（hf-mirror / HuggingFace 官方源）。

- 大文件（model.bin）分片并行下载，小文件直连（复用 utils.download）
- 断点续传：分片文件保留，重跑跳过已完成分片
- 大小校验：在线取 HF API 元数据，离线用内置表兜底
- 模型目录与推荐：常见型号清单（大小/显存），按显卡显存推荐档位
"""

from __future__ import annotations

import subprocess
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

import httpx

from .errors import VidarError
from .utils.download import CancelCheck, Progress, ProgressCallback, download_file

ENDPOINTS: dict[str, str] = {
    "hf-mirror": "https://hf-mirror.com",
    "huggingface": "https://huggingface.co",
}

# 常用别名 → HuggingFace 仓库
REPO_ALIASES: dict[str, str] = {
    "large-v3": "Systran/faster-whisper-large-v3",
    "large-v3-turbo": "Systran/faster-whisper-large-v3-turbo",
    "medium": "Systran/faster-whisper-medium",
    "small": "Systran/faster-whisper-small",
    "base": "Systran/faster-whisper-base",
    "tiny": "Systran/faster-whisper-tiny",
}

REQUIRED_FILES: tuple[str, ...] = (
    "model.bin",
    "config.json",
    "tokenizer.json",
    "vocabulary.json",
    "preprocessor_config.json",
)

# 离线兜底：已知模型各文件大小（字节），以本机实测为准
KNOWN_SIZES: dict[str, dict[str, int]] = {
    "Systran/faster-whisper-large-v3": {
        "model.bin": 3_087_284_237,
        "config.json": 2_394,
        "preprocessor_config.json": 340,
        "tokenizer.json": 2_480_617,
        "vocabulary.json": 1_068_114,
    },
}

BIG_FILE_MIN_BYTES = 100 * 1024 * 1024  # 未知模型时 model.bin 的合理下限


# --------------------------------------------------------------------------- #
# 模型目录与推荐（GUI「模型管理」页 / CLI model list|recommend 共用）
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class ModelSpec:
    alias: str
    repo: str
    approx_mb: int  # 近似总大小（MB）
    vram_gb: float  # 建议显存下限（GB，float16 推理粗略值）
    note: str


MODEL_CATALOG: tuple[ModelSpec, ...] = (
    ModelSpec("large-v3", "Systran/faster-whisper-large-v3", 3100, 6.0, "精度最高，推荐"),
    ModelSpec(
        "large-v3-turbo", "Systran/faster-whisper-large-v3-turbo", 1620, 4.0,
        "接近 large 的精度，速度更快",
    ),
    ModelSpec("medium", "Systran/faster-whisper-medium", 1530, 3.0, "中档，显存紧张时可用"),
    ModelSpec("small", "Systran/faster-whisper-small", 490, 2.0, "轻量，低显存 / CPU 可用"),
    ModelSpec("base", "Systran/faster-whisper-base", 150, 1.0, "最小可用，精度较低"),
)


def catalog() -> list[ModelSpec]:
    """常见模型清单（按推荐度排序）。"""
    return list(MODEL_CATALOG)


def find_spec(alias: str) -> ModelSpec | None:
    for spec in MODEL_CATALOG:
        if spec.alias == alias:
            return spec
    return None


def detect_vram_gb() -> float | None:
    """读取首块 NVIDIA GPU 的显存总量（GB）；无 nvidia-smi / 无 GPU 时返回 None。"""
    try:
        completed = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0 or not completed.stdout.strip():
        return None
    first_line = completed.stdout.strip().splitlines()[0].strip()
    with suppress(ValueError):
        return round(int(first_line) / 1024, 1)
    return None


def recommend_model(vram_gb: float | None, *, has_cuda: bool = False) -> tuple[ModelSpec, str]:
    """按显存推荐模型，返回（型号, 理由）。"""
    if vram_gb is not None:
        for spec in MODEL_CATALOG:
            if vram_gb >= spec.vram_gb:
                return (
                    spec,
                    f"检测到显存 {vram_gb:.1f} GB ≥ {spec.vram_gb:.0f} GB，推荐 {spec.alias}",
                )
        return (
            MODEL_CATALOG[-1],
            f"显存仅 {vram_gb:.1f} GB：推荐 {MODEL_CATALOG[-1].alias}，或改用 CPU（--device cpu）",
        )
    if has_cuda:
        turbo = find_spec("large-v3-turbo") or MODEL_CATALOG[1]
        return turbo, "有 CUDA 但未取到显存信息：保守推荐 large-v3-turbo"
    small = find_spec("small") or MODEL_CATALOG[3]
    return small, "未检测到 NVIDIA GPU：推荐 small（CPU 模式，速度较慢）"


def resolve_repo(model: str) -> str:
    """把别名 / 包名 / 仓库 id 统一成 HF 仓库 id。"""
    if model in REPO_ALIASES:
        return REPO_ALIASES[model]
    if "/" in model:
        return model
    if model.startswith("faster-whisper-"):
        return f"Systran/{model}"
    return model


def models_root() -> Path:
    """模型根目录：便携包取 exe 同级 models/，源码运行取当前目录。"""
    from .config import exe_dir

    return (exe_dir() or Path.cwd()) / "models"


def default_model_dir(model: str, base: Path | None = None) -> Path:
    name = resolve_repo(model).split("/")[-1]
    return (base or models_root()) / name


def expected_sizes(repo: str, *, endpoint: str | None = None) -> dict[str, int] | None:
    """优先在线取元数据，失败回退内置表（可能为 None）。"""
    if endpoint:
        with suppress(Exception), httpx.Client(timeout=15, follow_redirects=True) as client:
            response = client.get(f"{endpoint}/api/models/{repo}")
            response.raise_for_status()
            siblings = response.json().get("siblings") or []
            sizes = {
                str(item["rfilename"]): int(item["size"])
                for item in siblings
                if isinstance(item, dict)
                and item.get("rfilename")
                and isinstance(item.get("size"), int)
            }
            if sizes:
                return sizes
    return KNOWN_SIZES.get(repo)


@dataclass(frozen=True)
class CheckResult:
    name: str
    expected: int | None
    actual: int | None
    ok: bool


def check_model(model: str, model_dir: Path, *, endpoint: str | None = None) -> list[CheckResult]:
    """校验模型文件完整性（存在性 + 大小比对；无元数据时做合理性检查）。"""
    repo = resolve_repo(model)
    sizes = expected_sizes(repo, endpoint=endpoint) or {}
    results: list[CheckResult] = []
    for name in REQUIRED_FILES:
        path = model_dir / name
        actual = path.stat().st_size if path.exists() else None
        expected = sizes.get(name)
        if actual is None:
            ok = False
        elif expected is not None:
            ok = actual == expected
        elif name == "model.bin":
            ok = actual > BIG_FILE_MIN_BYTES
        else:
            ok = actual > 0
        results.append(CheckResult(name=name, expected=expected, actual=actual, ok=ok))
    return results


def download_model(
    model: str,
    model_dir: Path,
    *,
    source: str = "hf-mirror",
    connections: int = 6,
    on_progress: ProgressCallback | None = None,
    should_cancel: CancelCheck | None = None,
) -> tuple[list[str], list[str]]:
    """下载模型到 model_dir，返回 (已下载, 已跳过)。"""
    endpoint = ENDPOINTS.get(source, source)
    repo = resolve_repo(model)
    model_dir.mkdir(parents=True, exist_ok=True)

    sizes = expected_sizes(repo, endpoint=endpoint)
    if not sizes:
        raise VidarError(
            f"无法获取模型元数据：{repo}",
            hint="检查网络，或改用 --source huggingface；已知模型可用 large-v3/medium/small",
        )
    missing = [name for name in REQUIRED_FILES if name not in sizes]
    if missing:
        raise VidarError(f"仓库 {repo} 缺少必需文件：{', '.join(missing)}")

    progress = Progress()
    downloaded: list[str] = []
    skipped: list[str] = []

    for name in REQUIRED_FILES:
        size = sizes[name]
        target = model_dir / name
        if target.exists() and target.stat().st_size == size:
            skipped.append(name)
            continue
        download_file(
            f"{endpoint}/{repo}/resolve/main/{name}",
            target,
            size,
            description=name,
            connections=connections,
            progress=progress,
            on_progress=on_progress,
            should_cancel=should_cancel,
        )
        if target.stat().st_size != size:
            raise VidarError(f"{name} 下载后大小不符（期望 {size}，实际 {target.stat().st_size}）")
        downloaded.append(name)

    return downloaded, skipped


def ensure_model_available(model: str, model_dir: Path, *, source: str = "hf-mirror") -> None:
    """下载 + 校验一步到位，校验失败抛错。"""
    download_model(model, model_dir, source=source)
    results = check_model(model, model_dir)
    bad = [item.name for item in results if not item.ok]
    if bad:
        raise VidarError(f"模型校验未通过：{', '.join(bad)}")

"""faster-whisper 语音识别：模型加载、幻觉过滤、段落合并。"""

from __future__ import annotations

import contextlib
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .config import AsrSettings, exe_dir
from .errors import CancelledError, DependencyError, VidarError
from .models import AsrResult, AsrSegment, AsrWord
from .utils.text import format_clock

if TYPE_CHECKING:
    from .pipeline.context import RunContext

# 高频幻听短语：命中且片段很短时丢弃（长片段可能是真实内容，不误杀）
HALLUCINATION_PATTERNS = (
    "请点赞",
    "点赞投币",
    "一键三连",
    "字幕由",
    "字幕组",
    "字幕志愿者",
    "谢谢观看",
    "感谢观看",
    "下期再见",
    "订阅频道",
    "订阅我们",
    "thank you for watching",
    "thanks for watching",
    "subscribe",
    "amara.org",
    "subtitles by",
    "subtitle by",
)


def _cuda_available() -> bool:
    try:
        import ctranslate2  # type: ignore[import-not-found]

        return int(ctranslate2.get_cuda_device_count()) > 0
    except Exception:  # noqa: BLE001 - 不同版本 API 不稳定
        return False


def add_cuda_dll_dirs() -> None:
    """Windows：把 pip 安装（或便携包内置）的 CUDA 运行时 DLL 目录注册到搜索路径。

    CTranslate2 通过 LoadLibrary 加载 DLL，只认 PATH（add_dll_directory
    对标准的 LoadLibrary 调用无效），因此两种方式都做。
    """
    if os.name != "nt":
        return
    dirs = [str(folder) for folder in cuda_dll_dirs()]
    if not dirs:
        return
    current = os.environ.get("PATH", "")
    os.environ["PATH"] = os.pathsep.join(dirs) + os.pathsep + current
    for folder in dirs:
        with contextlib.suppress(OSError):
            os.add_dll_directory(folder)


_CUDA_PACKAGES = ("cublas", "cudnn", "cuda_runtime", "cuda_nvrtc")


def cuda_dll_dirs() -> list[Path]:
    """CUDA DLL 可能来自：pip nvidia-* 包 / PyInstaller 便携包内部目录。"""
    roots: list[Path] = []
    try:
        import nvidia  # type: ignore[import-not-found]  # namespace package

        roots.extend(Path(p) for p in getattr(nvidia, "__path__", []))
    except ImportError:
        pass
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        roots.append(Path(str(meipass)) / "nvidia")
    executable_dir = Path(sys.executable).resolve().parent
    roots.extend([executable_dir / "nvidia", executable_dir / "_internal" / "nvidia"])

    found: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        for package in _CUDA_PACKAGES:
            folder = root / package / "bin"
            key = str(folder).lower()
            if folder.is_dir() and key not in seen:
                seen.add(key)
                found.append(folder)
    return found


def resolve_model_reference(model: str) -> str:
    """解析 ASR 模型引用：HF ID / 本地目录 / 便携包内相对目录回退。"""
    candidate = Path(model)
    if candidate.is_dir():
        return str(candidate)
    name = candidate.name or model
    roots = [Path.cwd()]
    if (folder := exe_dir()) is not None:
        roots.append(folder)
    for root in roots:
        fallback = root / "models" / name
        if fallback.is_dir():
            return str(fallback)
    return model


def resolve_device(settings: AsrSettings) -> tuple[str, str]:
    """解析 device / compute_type 的 auto 逻辑。"""
    device = settings.device
    if device == "auto":
        device = "cuda" if _cuda_available() else "cpu"
    compute_type = settings.compute_type
    if compute_type == "auto":
        compute_type = "float16" if device == "cuda" else "int8"
    return device, compute_type


def prepare_environment(settings: AsrSettings) -> None:
    """HF 镜像、xet 传输方式与 CUDA DLL 搜索路径。"""
    if settings.hf_endpoint:
        os.environ["HF_ENDPOINT"] = settings.hf_endpoint
        os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
    add_cuda_dll_dirs()


def _wrap_gpu_error(exc: RuntimeError) -> VidarError:
    """把 CTranslate2 的 DLL 报错翻译成可执行的提示。"""
    message = str(exc)
    lowered = message.lower()
    if "dll" in lowered or "cublas" in lowered or "cudnn" in lowered:
        return DependencyError(
            f"CUDA 运行时异常：{message}",
            hint="先执行 uv sync --extra asr 安装 CUDA DLL；或改用 --device cpu（较慢）",
        )
    return VidarError(f"模型推理失败：{message}")


def is_hallucination(text: str, start: float, end: float) -> bool:
    stripped = text.strip().lower()
    if not stripped:
        return True
    if end - start > 3.0:  # 真实语句往往更长，避免误杀
        return False
    return any(pattern in stripped for pattern in HALLUCINATION_PATTERNS)


def merge_segments(
    segments: list[Any], *, max_chars: int = 40, max_gap: float = 1.2
) -> list[AsrSegment]:
    """把 whisper 碎段合并为阅读友好的段落：达到字数或遇到明显停顿即切段。"""
    merged: list[AsrSegment] = []
    buf_text: list[str] = []
    buf_words: list[AsrWord] = []
    start: float | None = None
    end = 0.0
    length = 0

    def flush() -> None:
        nonlocal buf_text, buf_words, start, end, length
        if start is None:
            return
        text = "".join(buf_text).strip()
        if text:
            merged.append(
                AsrSegment(idx=len(merged), start=start, end=end, text=text, words=buf_words)
            )
        buf_text, buf_words, start, end, length = [], [], None, 0.0, 0

    for index, seg in enumerate(segments):
        if start is None:
            start = seg.start
        buf_text.append(seg.text)
        for word in getattr(seg, "words", None) or []:
            buf_words.append(
                AsrWord(
                    start=word.start,
                    end=word.end,
                    text=word.word,  # faster-whisper 的 Word.word 是文本
                    probability=getattr(word, "probability", None),
                )
            )
        end = seg.end
        length += len(seg.text.strip())
        next_seg = segments[index + 1] if index + 1 < len(segments) else None
        gap = (next_seg.start - seg.end) if next_seg is not None else 999.0
        if length >= max_chars or gap >= max_gap:
            flush()
    flush()
    return merged


def transcribe(ctx: RunContext, wav: Path) -> AsrResult:
    settings = ctx.settings.asr
    prepare_environment(settings)
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:  # pragma: no cover
        raise DependencyError("缺少 faster-whisper", hint="uv sync --extra asr") from exc

    device, compute_type = resolve_device(settings)
    model_reference = resolve_model_reference(settings.model)
    if Path(model_reference) != Path(settings.model):
        ctx.log.info("模型路径回退：%s → %s", settings.model, model_reference)
    ctx.emit(
        "progress",
        step="asr",
        done=0,
        total=1,
        message=f"加载模型 {model_reference}（{device}/{compute_type}，首次运行自动下载）",
    )
    ctx.log.info("加载 ASR 模型：%s（%s / %s）", model_reference, device, compute_type)
    try:
        model = WhisperModel(model_reference, device=device, compute_type=compute_type)
    except RuntimeError as exc:
        raise _wrap_gpu_error(exc) from exc

    ctx.emit("progress", step="asr", done=0, total=1, message="开始识别…")
    segments_iter, info = model.transcribe(
        str(wav),
        language=settings.language or None,
        vad_filter=settings.vad,
        condition_on_previous_text=False,  # 防复读/幻觉
        word_timestamps=True,
        beam_size=settings.beam_size,
        initial_prompt=settings.initial_prompt or None,
    )

    duration = float(getattr(info, "duration", 0.0) or 0.0)
    raw_segments: list[Any] = []
    try:
        for seg in segments_iter:
            if ctx.cancel_event.is_set():
                raise CancelledError()
            raw_segments.append(seg)
            if duration > 0:
                ctx.emit(
                    "progress",
                    step="asr",
                    done=min(float(seg.end), duration),
                    total=duration,
                    message=f"已识别 {format_clock(seg.end)} / {format_clock(duration)}",
                )
    except RuntimeError as exc:
        raise _wrap_gpu_error(exc) from exc

    kept = [
        seg
        for seg in raw_segments
        if not (settings.filter_hallucinations and is_hallucination(seg.text, seg.start, seg.end))
    ]
    dropped = len(raw_segments) - len(kept)
    if dropped:
        ctx.log.info("过滤疑似幻听片段 %d 个", dropped)

    return AsrResult(
        engine="faster-whisper",
        model=model_reference,
        language=settings.language or "auto",
        duration_sec=duration or None,
        segments=merge_segments(kept),
    )

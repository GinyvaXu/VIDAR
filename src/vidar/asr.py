"""faster-whisper 语音识别：模型加载、幻觉过滤、段落合并。

支持暂停/续跑：逐段生成过程中可在段间挂起；「暂停并释放显存」时把已识别
段落写入 `asr.partial.json` 检查点，续跑时从上次结束时间点切片音频继续识别
（模型重新加载后不必从头再来）。
"""

from __future__ import annotations

import contextlib
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

from .config import AsrSettings, exe_dir
from .errors import CancelledError, DependencyError, PauseRelease, VidarError
from .models import AsrResult, AsrSegment, AsrWord
from .utils.checkpoint import clear_checkpoint, load_checkpoint, save_checkpoint
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


def cuda_available() -> bool:
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
    # 机器级共享目录：多版本便携包共用一份 GPU 运行时（vidar gpu install --shared）
    with contextlib.suppress(Exception):
        from .config import shared_data_dir

        roots.append(shared_data_dir() / "nvidia")

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


def _looks_like_path(value: str) -> bool:
    """看起来是本地路径（而非 HF 仓库名）：./ ../ / \\ ~ 或盘符开头。"""
    text = value.strip()
    if not text:
        return False
    if text.startswith((".", "/", "\\", "~")):
        return True
    return len(text) >= 2 and text[1] == ":"  # C:/... 或 C:\...


def resolve_model_reference(model: str) -> str:
    """解析 ASR 模型引用：HF ID / 本地目录 / 便携包内相对目录回退。

    - 已是目录 → 转为绝对路径；
    - 相对路径（如 ./models/faster-whisper-large-v3）依次相对 当前目录 / exe 目录 /
      机器级共享目录（如 %LOCALAPPDATA%/vidar）解析，并按「模型名」回退 models/<name>；
    - 明显是路径但哪里都找不到 → 抛清晰错误（下载 / 放入包内 models / 填绝对路径），
      避免 faster-whisper 把路径当 HF 仓库名报晦涩错误；
    - 其余（large-v3、Systran/faster-whisper-large-v3 等）原样交给 faster-whisper。
    """
    value = (model or "").strip()
    if not value:
        raise VidarError(
            "未配置 ASR 模型",
            hint="在 config.toml 的 [asr].model 填模型名（如 large-v3），或用模型页/命令下载",
        )
    candidate = Path(value).expanduser()
    if candidate.is_dir():
        return str(candidate.resolve())
    name = candidate.name or value
    roots: list[Path] = [Path.cwd()]
    if (folder := exe_dir()) is not None:
        roots.append(folder)
    with contextlib.suppress(Exception):
        from .config import shared_data_dir

        roots.append(shared_data_dir())
    tried: list[str] = []
    for root in roots:
        for fallback in (root / value, root / "models" / name):
            key = str(fallback)
            if key not in tried:
                tried.append(key)
            if fallback.is_dir():
                return str(fallback.resolve())
    if _looks_like_path(value):
        raise VidarError(
            f"本地模型目录不存在：{model}",
            hint=(
                "已尝试：" + "；".join(tried)
                + "\n解决任一即可：① 运行 VIDAR-CLI.exe model download large-v3 下载；"
                "② 把模型目录放到包内 models/ 下；③ config.toml [asr].model 填绝对路径"
            ),
        )
    return value


def resolve_device(settings: AsrSettings) -> tuple[str, str]:
    """解析 device / compute_type 的 auto 逻辑。"""
    device = settings.device
    if device == "auto":
        device = "cuda" if cuda_available() else "cpu"
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


def _slice_wav(source: Path, start_sec: float, target: Path) -> tuple[float, int]:
    """把 16k 单声道 WAV 从 start_sec 起裁到 target；返回（实际起点秒, 写入帧数）。"""
    import wave

    target.unlink(missing_ok=True)
    with wave.open(str(source), "rb") as reader:
        rate = reader.getframerate()
        channels = reader.getnchannels()
        width = reader.getsampwidth()
        total_frames = reader.getnframes()
        start_frame = min(max(0, int(start_sec * rate)), total_frames)
        reader.setpos(start_frame)
        frames = reader.readframes(total_frames - start_frame)
    with wave.open(str(target), "wb") as writer:
        writer.setnchannels(channels)
        writer.setsampwidth(width)
        writer.setframerate(rate)
        writer.writeframes(frames)
    written = len(frames) // max(1, width * channels)
    return start_frame / rate, written


def _wav_duration(wav: Path) -> float:
    import wave

    with contextlib.suppress(Exception), wave.open(str(wav), "rb") as reader:
        return reader.getnframes() / max(1, reader.getframerate())
    return 0.0


def _dump_raw_segment(seg: Any) -> dict:
    """whisper 段 → 可序列化 dict（供检查点保存）。"""
    return {
        "start": float(seg.start),
        "end": float(seg.end),
        "text": str(seg.text),
        "words": [
            {
                "start": float(getattr(word, "start", 0.0) or 0.0),
                "end": float(getattr(word, "end", 0.0) or 0.0),
                "word": str(getattr(word, "word", "") or ""),
                "probability": getattr(word, "probability", None),
            }
            for word in (getattr(seg, "words", None) or [])
        ],
    }


def _load_raw_segment(payload: dict) -> SimpleNamespace:
    """检查点 dict → 轻量对象（接口与 whisper 段兼容，供 merge_segments 使用）。"""
    words = [
        SimpleNamespace(
            start=float(item.get("start", 0.0)),
            end=float(item.get("end", 0.0)),
            word=str(item.get("word", "")),
            probability=item.get("probability"),
        )
        for item in (payload.get("words") or [])
        if isinstance(item, dict)
    ]
    return SimpleNamespace(
        start=float(payload.get("start", 0.0)),
        end=float(payload.get("end", 0.0)),
        text=str(payload.get("text", "")),
        words=words,
    )


def transcribe(ctx: RunContext, wav: Path) -> AsrResult:
    settings = ctx.settings.asr
    prepare_environment(settings)
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:  # pragma: no cover
        raise DependencyError("缺少 faster-whisper", hint="uv sync --extra asr") from exc

    # ---- 检查点：暂停释放后从上次结束点继续 ----
    partial_path = ctx.artifact("asr.partial.json")
    partial = load_checkpoint(partial_path) or {}
    saved_segments: list[dict] = [
        item for item in (partial.get("segments") or []) if isinstance(item, dict)
    ]
    resume_from = float(partial.get("resume_from") or 0.0)
    language_hint = str(partial.get("language") or "")
    audio_duration = _wav_duration(wav)

    if resume_from > 0.5 and audio_duration and resume_from >= audio_duration - 0.5:
        # 已全部识别：直接由检查点组装（不加载模型）
        ctx.log.info("检查点已覆盖全部音频（%.1fs），直接组装识别结果", resume_from)
        result = _build_result(ctx, settings, saved_segments, [], language_hint)
        clear_checkpoint(partial_path)
        return result

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

    # ---- 续跑：从检查点时间点切片音频 ----
    work_wav = wav
    offset = 0.0
    if resume_from > 0.5 and saved_segments:
        sliced = ctx.artifact("audio.resume.wav")
        offset, frames_written = _slice_wav(wav, resume_from, sliced)
        if frames_written <= 0:
            ctx.log.info("检查点已到音频末尾，直接组装识别结果")
            result = _build_result(ctx, settings, saved_segments, [], language_hint)
            clear_checkpoint(partial_path)
            return result
        work_wav = sliced
        ctx.log.info(
            "检测到检查点：从 %.1fs 继续识别（已保存 %d 段）", offset, len(saved_segments)
        )

    language = (settings.language or "").strip().lower()
    if language in ("", "auto", "detect", "none"):
        language = language_hint or None  # 续跑时沿用首次检测的语言

    ctx.emit("progress", step="asr", done=0, total=1, message="开始识别…")
    segments_iter, info = model.transcribe(
        str(work_wav),
        language=language,
        vad_filter=settings.vad,
        condition_on_previous_text=False,  # 防复读/幻觉
        word_timestamps=True,
        beam_size=settings.beam_size,
        initial_prompt=settings.initial_prompt or None,
    )

    duration = float(getattr(info, "duration", 0.0) or 0.0)
    total_duration = offset + duration
    raw_segments: list[Any] = []

    def save_progress() -> None:
        last_end = (
            offset + float(raw_segments[-1].end) if raw_segments else resume_from
        )
        save_checkpoint(
            partial_path,
            {
                "segments": saved_segments + [_dump_raw_segment(seg) for seg in raw_segments],
                "resume_from": last_end,
                "language": str(getattr(info, "language", "") or language_hint or ""),
            },
        )

    try:
        for seg in segments_iter:
            if ctx.cancel_event.is_set():
                raise CancelledError()
            try:
                ctx.wait_if_paused()
            except PauseRelease:
                save_progress()
                raise
            raw_segments.append(seg)
            if len(raw_segments) % 20 == 0:
                save_progress()
            if total_duration > 0:
                ctx.emit(
                    "progress",
                    step="asr",
                    done=min(offset + float(seg.end), total_duration),
                    total=total_duration,
                    message=(
                        f"已识别 {format_clock(offset + seg.end)} / {format_clock(total_duration)}"
                    ),
                )
    except RuntimeError as exc:
        raise _wrap_gpu_error(exc) from exc

    detected = str(getattr(info, "language", "") or "") or language_hint
    if detected:
        ctx.log.info(
            "识别语言：%s（置信度 %.2f）",
            detected,
            getattr(info, "language_probability", 0.0),
        )

    result = _build_result(ctx, settings, saved_segments, raw_segments, detected)
    clear_checkpoint(partial_path)
    ctx.artifact("audio.resume.wav").unlink(missing_ok=True)
    return result


def _build_result(
    ctx: RunContext,
    settings: AsrSettings,
    saved_segments: list[dict],
    raw_segments: list[Any],
    language: str,
) -> AsrResult:
    """合并「检查点段 + 本轮新段」：幻觉过滤（仅新段）→ 段落合并 → AsrResult。"""
    kept: list[Any] = [_load_raw_segment(item) for item in saved_segments]
    for seg in raw_segments:
        if settings.filter_hallucinations and is_hallucination(seg.text, seg.start, seg.end):
            continue
        kept.append(seg)
    dropped = len(raw_segments) - (len(kept) - len(saved_segments))
    if dropped:
        ctx.log.info("过滤疑似幻听片段 %d 个", dropped)
    merged = merge_segments(kept)
    duration = merged[-1].end if merged else None
    return AsrResult(
        engine="faster-whisper",
        model=resolve_model_reference(settings.model),
        language=language or "auto",
        duration_sec=duration,
        segments=merged,
    )

"""VIDAR 异常体系。"""

from __future__ import annotations


class VidarError(Exception):
    """所有业务异常的基类。"""

    def __init__(self, message: str, *, hint: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint

    def __str__(self) -> str:  # pragma: no cover - 展示用
        return f"{self.message}（{self.hint}）" if self.hint else self.message


class ConfigError(VidarError):
    """配置文件格式错误、路径不可用等。"""


class DependencyError(VidarError):
    """外部依赖缺失：ffmpeg / yt-dlp / faster-whisper 等。"""


class NetworkError(VidarError):
    """下载或网络请求失败。"""


class LlmError(VidarError):
    """LLM 调用失败（重试后仍失败、返回不可解析等）。"""


class StepFailed(VidarError):
    """流水线步骤执行失败。"""

    def __init__(self, step: str, message: str, *, hint: str = "") -> None:
        super().__init__(f"步骤「{step}」失败：{message}", hint=hint)
        self.step = step


class MilestoneError(VidarError):
    """功能尚未实现（属于后续里程碑）。"""

    def __init__(self, milestone: str, feature: str) -> None:
        super().__init__(
            f"功能「{feature}」计划在 {milestone} 实现，当前版本尚未支持",
            hint="可先用 --dry-run 查看完整执行计划",
        )
        self.milestone = milestone
        self.feature = feature


class CancelledError(VidarError):
    """用户主动取消任务（已完成步骤的产物保留，可重跑续传）。"""

    def __init__(self, message: str = "任务已取消") -> None:
        super().__init__(message, hint="已完成步骤不会丢失，重跑将从中断处继续")

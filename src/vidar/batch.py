"""批量任务队列：多链接顺序处理，失败不中断，产出汇总。

设计：
- 一次只跑一条（与流水线单实例语义一致），前一条失败默认继续下一条；
- 每条任务复用标准 RunContext/PipelineRunner（断点续跑语义天然继承）；
- 事件回调（供 GUI / CLI 展示）：batch_start / batch_item / batch_finished。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from .config import Settings
from .errors import CancelledError, VidarError


@dataclass
class BatchItem:
    source: str
    status: str = "pending"  # pending | running | done | failed | cancelled
    error: str = ""


@dataclass
class BatchResult:
    items: list[BatchItem] = field(default_factory=list)

    @property
    def done_count(self) -> int:
        return sum(1 for item in self.items if item.status == "done")

    @property
    def failed(self) -> list[BatchItem]:
        return [item for item in self.items if item.status == "failed"]


def read_sources(path: Path) -> list[str]:
    """读取链接清单：每行一个；# 开头为注释；去重且保持顺序。"""
    if not path.exists():
        raise VidarError(
            f"清单文件不存在：{path}",
            hint="创建文本文件，每行一个 BV 号/链接（# 开头为注释）",
        )
    items: list[str] = []
    seen: set[str] = set()
    # utf-8-sig：兼容 Windows 记事本/PowerShell 写出的 BOM 清单
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line not in seen:
            seen.add(line)
            items.append(line)
    if not items:
        raise VidarError(f"清单文件没有有效链接：{path}")
    return items


def run_batch(
    settings: Settings,
    sources: Sequence[str],
    *,
    on_event: Callable[[str, dict], None] | None = None,
    from_step: str | None = None,
    to_step: str | None = None,
    stop_on_error: bool = False,
) -> BatchResult:
    """顺序处理多个来源，返回汇总结果。"""
    from .pipeline import PipelineRunner, RunContext, default_steps

    result = BatchResult(items=[BatchItem(source=source) for source in sources])
    total = len(result.items)

    def emit(event: str, **data: object) -> None:
        if on_event is not None:
            on_event(event, data)

    emit("batch_start", total=total)
    for index, item in enumerate(result.items, start=1):
        item.status = "running"
        emit("batch_item", index=index, total=total, source=item.source, status="running")
        try:
            ctx = RunContext(settings, item.source)

            def forward(event: str, data: dict, _index: int = index) -> None:
                emit(event, **data, batch_index=_index, batch_total=total)

            ctx.on_event = forward
            PipelineRunner(ctx, default_steps()).run(  # type: ignore[arg-type]
                from_step=from_step, to_step=to_step
            )
            item.status = "done"
        except CancelledError:
            item.status = "cancelled"
            item.error = "已取消"
            emit("batch_item", index=index, total=total, source=item.source, status="cancelled")
            break
        except VidarError as exc:
            item.status = "failed"
            item.error = exc.message
            if stop_on_error:
                emit(
                    "batch_item",
                    index=index,
                    total=total,
                    source=item.source,
                    status="failed",
                    error=item.error,
                )
                break
        except Exception as exc:  # noqa: BLE001 - 兜底，保证队列不中断
            item.status = "failed"
            item.error = f"{type(exc).__name__}: {exc}"
            if stop_on_error:
                emit(
                    "batch_item",
                    index=index,
                    total=total,
                    source=item.source,
                    status="failed",
                    error=item.error,
                )
                break
        emit(
            "batch_item",
            index=index,
            total=total,
            source=item.source,
            status=item.status,
            error=item.error,
        )
    emit("batch_finished", total=total, done=result.done_count, failed=len(result.failed))
    return result


__all__ = ["BatchItem", "BatchResult", "read_sources", "run_batch"]

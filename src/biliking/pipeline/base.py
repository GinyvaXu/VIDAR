"""Step 协议与顺序执行器。

执行语义：
- 按声明顺序执行 8 个步骤；
- done 且产物完整的步骤自动跳过（断点续跑）；
- --from <step> 会把该步骤及其后续全部重置后重跑；
- 每步执行前后更新 state.json，失败不破坏已完成产物。
"""

from __future__ import annotations

import time
from typing import Protocol

from rich.console import Console
from rich.table import Table

from ..errors import BilikingError, MilestoneError
from ..models import StepStatus
from .context import RunContext

console = Console()


class Step(Protocol):
    name: str
    title: str
    milestone: str | None  # None 表示已实现

    def run(self, ctx: RunContext) -> dict[str, str]:
        """执行步骤，返回 {逻辑名: work 目录下的相对文件名}。"""
        ...


class PipelineRunner:
    def __init__(self, ctx: RunContext, steps: list[Step]) -> None:
        self.ctx = ctx
        self.steps = steps
        self.order = [step.name for step in steps]

    # ------------------------------------------------------------------ #
    def select(self, from_step: str | None = None, to_step: str | None = None) -> list[Step]:
        if from_step and from_step not in self.order:
            raise BilikingError(f"未知步骤：{from_step}", hint=f"可选：{', '.join(self.order)}")
        if to_step and to_step not in self.order:
            raise BilikingError(f"未知步骤：{to_step}", hint=f"可选：{', '.join(self.order)}")
        start = self.order.index(from_step) if from_step else 0
        end = self.order.index(to_step) if to_step else len(self.steps) - 1
        if start > end:
            raise BilikingError(f"--from {from_step} 在 --to {to_step} 之后，无法执行")
        return self.steps[start : end + 1]

    def render_plan(self, selected: list[Step]) -> None:
        table = Table(title=f"执行计划 · {self.ctx.key}", show_lines=False)
        table.add_column("步骤", style="cyan", no_wrap=True)
        table.add_column("内容")
        table.add_column("状态", no_wrap=True)
        table.add_column("里程碑", justify="center", no_wrap=True)
        for step in selected:
            if self.ctx.state.is_done(step.name):
                status = "[green]已完成（跳过）[/green]"
            else:
                status = self.ctx.state.status(step.name).value
            milestone = step.milestone or "[green]已实现[/green]"
            table.add_row(step.name, step.title, status, milestone)
        console.print(table)

    # ------------------------------------------------------------------ #
    def run(
        self,
        *,
        from_step: str | None = None,
        to_step: str | None = None,
        dry_run: bool = False,
    ) -> None:
        selected = self.select(from_step, to_step)
        if from_step:
            reset = self.ctx.state.reset_from(from_step, self.order)
            if reset:
                console.print(f"[yellow]已重置步骤：{', '.join(reset)}[/yellow]")

        self.render_plan(selected)
        if dry_run:
            console.print("[dim]--dry-run：仅展示计划，未执行任何操作[/dim]")
            return

        for step in selected:
            if self.ctx.state.is_done(step.name):
                self.ctx.log.info("跳过 %s（已完成）", step.name)
                continue
            self._run_step(step)

    def _run_step(self, step: Step) -> None:
        self.ctx.state.mark_running(step.name)
        started = time.perf_counter()
        self.ctx.log.info("开始 %s（%s）", step.name, step.title)
        try:
            artifacts = step.run(self.ctx)
        except MilestoneError as exc:
            self.ctx.state.mark_failed(step.name, str(exc))
            console.print(f"[yellow]⏭ {exc}[/yellow]")
            raise
        except BilikingError as exc:
            self.ctx.state.mark_failed(step.name, exc.message)
            raise
        except Exception as exc:  # noqa: BLE001 - 兜底，防止状态悬挂
            self.ctx.state.mark_failed(step.name, f"{type(exc).__name__}: {exc}")
            raise BilikingError(f"步骤「{step.title}」发生未预期错误：{exc}") from exc

        elapsed = time.perf_counter() - started
        self.ctx.state.mark_done(step.name, artifacts)
        self.ctx.log.info("完成 %s，用时 %.1fs", step.name, elapsed)

    # ------------------------------------------------------------------ #
    def summary_rows(self) -> list[tuple[str, str, str]]:
        rows: list[tuple[str, str, str]] = []
        for step in self.steps:
            record = self.ctx.state.record(step.name)
            status = record.status
            if status is StepStatus.DONE:
                label = "[green]done[/green]"
            elif status is StepStatus.FAILED:
                label = "[red]failed[/red]"
            elif status is StepStatus.RUNNING:
                label = "[yellow]running[/yellow]"
            else:
                label = "[dim]pending[/dim]"
            rows.append((step.name, step.title, label))
        return rows

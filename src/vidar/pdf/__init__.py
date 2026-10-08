"""PDF 渲染入口：DocData 数据装载 + 模板分发。"""

from __future__ import annotations

from pathlib import Path

from ..errors import DependencyError, VidarError
from ..frames import ExtractedFrame
from ..models import AsrResult, RefineResult, SummaryResult, VideoMeta


class DocData:
    """PDF / DOCX 渲染所需的全部数据（从 work 目录产物组装）。"""

    output_path: Path  # 由渲染函数注入

    def __init__(
        self,
        *,
        meta: VideoMeta,
        summary: SummaryResult,
        refine: RefineResult,
        asr: AsrResult,
        frames: list[ExtractedFrame],
        images_dir: Path,
        mode: str = "full",
        style: str = "newspaper",
    ) -> None:
        self.meta = meta
        self.summary = summary
        self.refine = refine
        self.asr = asr
        self.frames = frames
        self.images_dir = images_dir
        self.mode = mode if mode in {"full", "brief"} else "full"
        self.style = style if style in {"newspaper", "paper"} else "newspaper"

    # ------------------------------------------------------------------ #
    def chapter_paragraphs(self, chapter) -> list[tuple[float, str]]:
        """章内精修段落（按时间归属）。"""
        start = chapter.start
        end = chapter.end if chapter.end is not None else float("inf")
        return [
            (segment.start, segment.refined_text)
            for segment in self.refine.segments
            if start <= segment.start < end
        ]

    def chapter_figures(self, chapter) -> list[tuple[float, str, Path]]:
        """章内配图（时间, 图注, 文件路径）。"""
        result: list[tuple[float, str, Path]] = []
        for hint in chapter.figures:
            for frame in self.frames:
                if abs(frame.start - hint.start) < 0.5:
                    path = self.images_dir / frame.filename
                    if path.exists():
                        result.append((hint.start, frame.caption or hint.caption, path))
                    break
        return result

    def chapter_quote(self, chapter) -> str:
        """章节金句（与小结重复时自动去重，避免排版冗余）。"""
        quote = (chapter.quote or "").strip()
        summary = (chapter.summary or "").strip()
        if quote and summary and (quote in summary or summary in quote):
            return ""
        return quote

    def cover_image(self) -> Path | None:
        cover = self.images_dir / "cover.jpg"
        return cover if cover.exists() else None

    @property
    def keywords(self) -> list[str]:
        return [tag for tag in self.meta.tags if tag][:6]


def render_pdf(data: DocData, output_path: Path) -> Path:
    """按 style 渲染 PDF，返回产物路径。"""
    try:
        import reportlab  # noqa: F401
    except ImportError as exc:  # pragma: no cover
        raise DependencyError("缺少 PDF 依赖 reportlab", hint="执行 uv sync 安装") from exc

    data.output_path = output_path
    if data.style == "paper":
        from .paper import build_paper

        return build_paper(data)

    from .newspaper import build_newspaper

    return build_newspaper(data)


def render_docx(data: DocData, output_path: Path) -> Path:
    """渲染 Word 文档（.docx），返回产物路径。"""
    try:
        import docx  # noqa: F401
    except ImportError as exc:  # pragma: no cover
        raise DependencyError("缺少 Word 依赖 python-docx", hint="执行 uv sync 安装") from exc

    from .docx_export import build_docx

    return build_docx(data, output_path)


__all__ = ["DocData", "render_pdf", "render_docx", "VidarError"]

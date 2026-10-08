"""PDF 预览：把 PDF 每页渲染成 PNG（排版检查用，依赖 dev 组 pymupdf）。

用法：uv run python scripts/preview_pdf.py <pdf 路径> [输出目录] [起始页] [页数]
"""

from __future__ import annotations

import sys
from pathlib import Path


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if len(sys.argv) < 2:
        print("用法：python scripts/preview_pdf.py <pdf> [输出目录] [起始页=1] [页数=8]")
        return 2

    pdf_path = Path(sys.argv[1])
    if not pdf_path.exists():
        print(f"文件不存在：{pdf_path}")
        return 1
    out_dir = Path(sys.argv[2]) if len(sys.argv) > 2 else pdf_path.parent / "preview"
    start = int(sys.argv[3]) if len(sys.argv) > 3 else 1
    count = int(sys.argv[4]) if len(sys.argv) > 4 else 8

    import pymupdf  # type: ignore[import-not-found]

    out_dir.mkdir(parents=True, exist_ok=True)
    with pymupdf.open(str(pdf_path)) as document:
        total = document.page_count
        end = min(total, start - 1 + count)
        for index in range(start - 1, end):
            page = document.load_page(index)
            pixmap = page.get_pixmap(dpi=110)
            target = out_dir / f"page-{index + 1:02d}.png"
            pixmap.save(str(target))
            print(f"✓ {target}（{pixmap.width}x{pixmap.height}）")
        print(f"共 {total} 页，已导出 {end - start + 1} 页到 {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

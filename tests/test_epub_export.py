"""EPUB 导出测试：结构合规 + 微信读书兼容守卫（书内全 ASCII）+ 全文/精简。"""

from __future__ import annotations

import zipfile
from datetime import datetime
from pathlib import Path
from xml.etree import ElementTree

from PIL import Image

from vidar.epub_export import render_epub
from vidar.export import output_filename
from vidar.frames import ExtractedFrame
from vidar.models import (
    AsrResult,
    Chapter,
    FigureHint,
    KeyPoint,
    RefinedSegment,
    RefineResult,
    SummaryResult,
    VideoMeta,
)
from vidar.pdf import DocData

XHTML_NS = {"x": "http://www.w3.org/1999/xhtml"}


def _doc_data(tmp_path: Path, *, with_cover: bool = True, mode: str = "full") -> DocData:
    images = tmp_path / "images"
    images.mkdir(parents=True, exist_ok=True)
    if with_cover:
        Image.new("RGB", (640, 360), (120, 140, 160)).save(images / "cover.jpg")
    Image.new("RGB", (640, 360), (80, 90, 100)).save(images / "fig01.jpg")

    meta = VideoMeta(
        bvid="BV1xx411c7mD",
        title="测试视频：EPUB 导出「书名」",
        up_name="测试UP",
        url="https://www.bilibili.com/video/BV1xx411c7mD",
        pubdate=datetime(2026, 5, 1),
        duration_sec=754.0,
        tags=["测试", "排版"],
    )
    chapters = [
        Chapter(
            index=0,
            title="开场与背景",
            start=0.0,
            end=20.0,
            summary="介绍主题与背景。",
            quote="这是一句金句。",
            figures=[FigureHint(start=10.0, caption="示意图", anchor=1)],
        ),
        Chapter(index=1, title="核心内容", start=20.0, end=40.0, summary="展开核心内容。"),
    ]
    summary = SummaryResult(
        model="fake",
        one_liner="一句话结论。",
        key_points=[KeyPoint(text="要点一", start=10.0), KeyPoint(text="要点二", start=30.0)],
        chapters=chapters,
    )
    segments = [
        RefinedSegment(
            idx=index,
            start=index * 10.0,
            end=index * 10.0 + 9.0,
            raw_text=f"原始第{index}段",
            refined_text=f"精修后的第{index}段文字，用于 EPUB 测试。",
        )
        for index in range(4)
    ]
    return DocData(
        meta=meta,
        summary=summary,
        refine=RefineResult(model="fake", segments=segments),
        asr=AsrResult(engine="fake", model="fake", segments=[]),
        frames=[ExtractedFrame(index=1, start=10.0, caption="示意图", filename="fig01.jpg")],
        images_dir=images,
        mode=mode,
    )


def _open_epub(path: Path) -> zipfile.ZipFile:
    return zipfile.ZipFile(path)


def test_output_filename_epub() -> None:
    assert output_filename("epub", "测试") == "电子书版-测试.epub"


def test_epub_structure(tmp_path: Path) -> None:
    data = _doc_data(tmp_path)
    output = tmp_path / "book.epub"
    render_epub(data, output)

    with _open_epub(output) as archive:
        names = archive.namelist()
        infos = archive.infolist()

        # mimetype 必须居首位且不压缩
        assert names[0] == "mimetype"
        assert infos[0].compress_type == zipfile.ZIP_STORED
        assert archive.read("mimetype") == b"application/epub+zip"

        assert "META-INF/container.xml" in names
        assert "OEBPS/content.opf" in names
        assert "OEBPS/nav.xhtml" in names
        assert "OEBPS/toc.ncx" in names
        assert "OEBPS/style.css" in names

        # 微信读书兼容守卫：书内文件名/结构全 ASCII（中文只允许出现在正文内容里）
        assert all(name.isascii() for name in names), "EPUB 内部文件名必须全 ASCII"

        # 所有 XHTML / XML 必须可解析
        for name in names:
            if name.endswith((".xhtml", ".ncx", ".opf", ".xml")):
                ElementTree.fromstring(archive.read(name))

        # OPF 关键声明
        opf = archive.read("OEBPS/content.opf").decode("utf-8")
        assert 'properties="cover-image"' in opf
        assert 'properties="nav"' in opf
        assert 'toc="ncx"' in opf
        assert "<dc:title>测试视频：EPUB 导出「书名」</dc:title>" in opf

        # 目录（nav + ncx）包含章节条目
        nav = archive.read("OEBPS/nav.xhtml").decode("utf-8")
        assert "1. 开场与背景" in nav and "2. 核心内容" in nav
        ncx = archive.read("OEBPS/toc.ncx").decode("utf-8")
        assert "开场与背景" in ncx

        # CSS 不含中文（属性级兼容）
        assert archive.read("OEBPS/style.css").decode("utf-8").isascii()

        # 图片齐全：清单里引用的图都能在包内找到
        assert "OEBPS/images/cover.jpg" in names
        assert "OEBPS/images/fig01.jpg" in names
        for page in ("OEBPS/preface.xhtml", "OEBPS/chapter-01.xhtml"):
            content = archive.read(page).decode("utf-8")
            for token in content.split('src="')[1:]:
                src = token.split('"')[0]
                assert f"OEBPS/{src}" in names, f"{page} 引用的 {src} 不在包内"


def test_epub_full_vs_brief(tmp_path: Path) -> None:
    full = tmp_path / "full.epub"
    brief = tmp_path / "brief.epub"
    render_epub(_doc_data(tmp_path / "a", mode="full"), full)
    render_epub(_doc_data(tmp_path / "b", mode="brief"), brief)

    with _open_epub(full) as archive:
        assert "精修后的第0段文字" in archive.read("OEBPS/chapter-01.xhtml").decode("utf-8")
    with _open_epub(brief) as archive:
        chapter = archive.read("OEBPS/chapter-01.xhtml").decode("utf-8")
        assert "精修后的第0段文字" not in chapter
        assert "介绍主题与背景" in chapter  # 章节小结仍在


def test_epub_without_cover(tmp_path: Path) -> None:
    output = tmp_path / "nocover.epub"
    render_epub(_doc_data(tmp_path, with_cover=False), output)

    with _open_epub(output) as archive:
        opf = archive.read("OEBPS/content.opf").decode("utf-8")
        assert "cover-image" not in opf
        assert "OEBPS/cover.xhtml" not in archive.namelist()
        ElementTree.fromstring(archive.read("OEBPS/nav.xhtml"))

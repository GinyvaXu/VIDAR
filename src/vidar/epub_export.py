"""EPUB 3 电子书导出：微信读书 / 电子墨水阅读器友好。

设计约定（均为实测经验）：
- 书内**所有标识符、属性名、文件名一律英文/ASCII**（中文只出现在可见正文）：
  社区实测「属性带中文」会导致微信读书 / Apple Books 解析异常（典型症状：图片不显示）；
- 标准 EPUB 3：mimetype（未压缩、首位）+ META-INF/container.xml + OPF + nav.xhtml，
  并附 toc.ncx 兼容旧阅读器；
- 重排排版：相对字号/行高、首行缩进、图片 max-width:100%，不内嵌字体（字号由阅读器控制）；
- 结构：封面 → 书名页 → 目录 → 导读（一句话+要点）→ 各章（小结/金句/章节配图/正文）→ 尾页。
"""

from __future__ import annotations

import html
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape

from . import __version__
from .pdf import DocData
from .utils.text import format_clock

_CSS = """\
body { font-family: serif; line-height: 1.75; margin: 0; padding: 0 0.4em; color: #111; }
h1 { font-size: 1.45em; font-weight: bold; line-height: 1.35; margin: 0.2em 0 0.5em; }
h2 { font-size: 1.15em; font-weight: bold; margin: 1em 0 0.4em; }
p { margin: 0 0 0.6em; text-indent: 2em; }
p.meta, p.lead, p.center, figcaption, li { text-indent: 0; }
p.meta { color: #666; font-size: 0.85em; }
p.lead { color: #444; }
p.src { font-size: 0.88em; color: #555; }
ol.points { padding-left: 1.4em; margin: 0.4em 0 0.8em; }
ol.points li { margin-bottom: 0.55em; }
ol.toc-list { list-style: none; padding-left: 0.4em; margin: 0.3em 0; }
ol.toc-list li { margin-bottom: 0.35em; }
a { color: #1a4f8a; text-decoration: none; }
a.ts { color: #666; font-size: 0.85em; }
blockquote.quote { margin: 0.8em 0; padding: 0.05em 0 0.05em 0.9em; border-left: 3px solid #999;
  color: #444; }
blockquote.quote p { text-indent: 0; margin: 0.3em 0; }
figure { margin: 1em 0; text-align: center; break-inside: avoid; page-break-inside: avoid; }
figure img { max-width: 100%; height: auto; }
figcaption { font-size: 0.85em; color: #666; margin-top: 0.4em; }
div.cover { margin: 0; padding: 0; text-align: center; }
div.cover img { max-width: 100%; height: auto; }
div.titlepage { text-align: center; margin-top: 15%; }
div.titlepage h1 { font-size: 1.8em; }
hr.rule { border: 0; border-top: 1px solid #bbb; margin: 1.2em 0; }
"""

_CONTAINER = """\
<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
"""

_PAGE_TEMPLATE = """\
<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" \
xml:lang="zh-CN" lang="zh-CN">
<head>
  <meta charset="utf-8"/>
  <title>{title}</title>
  <link rel="stylesheet" type="text/css" href="style.css"/>
</head>
<body>
{body}
</body>
</html>
"""


def _attr(value: object) -> str:
    """XML 属性值转义（含引号）。"""
    return html.escape(str(value), quote=True)


def _page(title: str, body: str) -> str:
    return _PAGE_TEMPLATE.replace("{title}", xml_escape(title)).replace("{body}", body)


def _link_at(url: str, start: float) -> str:
    """B 站跳转链接（带时间秒）。"""
    if not url:
        return ""
    separator = "&" if "?" in url else "?"
    return f"{url}{separator}t={int(max(0.0, start))}"


def _figure_html(number: int, caption: str, start: float, path: Path) -> str:
    label = f"图 {number}：{caption}" if caption else f"图 {number}"
    return (
        "<figure>"
        f'<img src="images/{_attr(path.name)}" alt="{_attr(label)}"/>'
        f"<figcaption>{xml_escape(label)}（{format_clock(start)}）</figcaption>"
        "</figure>"
    )


def _cover_page(data: DocData) -> str | None:
    cover = data.cover_image()
    if cover is None:
        return None
    return _page(
        "封面",
        f'<div class="cover"><img src="images/{_attr(cover.name)}" alt="封面"/></div>',
    )


def _titlepage(data: DocData) -> str:
    meta = data.meta
    lines = [f"作者：{meta.up_name}" if meta.up_name else "作者：—"]
    info: list[str] = []
    if meta.pubdate:
        info.append(meta.pubdate.strftime("%Y-%m-%d"))
    if meta.duration_sec:
        info.append(f"时长 {format_clock(meta.duration_sec)}")
    if info:
        lines.append(" · ".join(info))
    body = [
        '<div class="titlepage">',
        f"<h1>{xml_escape(meta.title)}</h1>",
        f'<p class="meta">{"<br/>".join(xml_escape(line) for line in lines)}</p>',
    ]
    if meta.url:
        body.append(f'<p class="meta"><a href="{_attr(meta.url)}">原视频链接</a></p>')
    body.append(f'<p class="meta">由 VIDAR v{__version__} 整理</p>')
    body.append("</div>")
    return _page("书名页", "\n".join(body))


def _preface_page(data: DocData) -> str:
    body: list[str] = ["<h1>导读</h1>"]
    if data.summary.one_liner:
        body.append(f'<p class="lead">{xml_escape(data.summary.one_liner)}</p>')
    if data.keywords:
        body.append(f'<p class="meta">关键词：{xml_escape(" / ".join(data.keywords))}</p>')
    if data.summary.key_points:
        body.append("<h2>核心要点</h2>")
        body.append('<ol class="points">')
        for point in data.summary.key_points[:12]:
            text = xml_escape(point.text)
            if point.start is not None and data.meta.url:
                link = _link_at(data.meta.url, point.start)
                text += f' <a class="ts" href="{_attr(link)}">[{format_clock(point.start)}]</a>'
            body.append(f"<li>{text}</li>")
        body.append("</ol>")
    return _page("导读", "\n".join(body))


def _chapter_page(data: DocData, chapter, figure_number: int) -> tuple[str, int]:
    body: list[str] = [f"<h1>{chapter.index + 1}　{xml_escape(chapter.title)}</h1>"]
    body.append(f'<p class="meta">章节时间 [{format_clock(chapter.start)}]</p>')
    if chapter.summary:
        body.append(f'<p class="lead">{xml_escape(chapter.summary)}</p>')
    quote = data.chapter_quote(chapter)
    if quote:
        body.append(f'<blockquote class="quote"><p>{xml_escape(quote)}</p></blockquote>')
    for start, caption, path in data.chapter_figures(chapter):
        body.append(_figure_html(figure_number, caption, start, path))
        figure_number += 1
    if data.mode == "full":
        for _start, text, source in data.chapter_paragraphs(chapter):
            body.append(f"<p>{xml_escape(text)}</p>")
            if source:
                body.append(f'<p class="src">{xml_escape(source)}</p>')
    return _page(chapter.title, "\n".join(body)), figure_number


def _appendix_page(data: DocData) -> str:
    meta = data.meta
    body = ["<h1>关于本书</h1>"]
    if meta.url:
        body.append(f'<p>原视频：<a href="{_attr(meta.url)}">{xml_escape(meta.bvid)}</a></p>')
    body.append(f"<p>整理工具：VIDAR v{__version__}（本地识别 + 大模型整理）</p>")
    body.append(f"<p>生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}</p>")
    body.append(
        '<p class="meta">本文档由 AI 自动整理生成，仅供个人学习研究使用；'
        "内容版权归原作者所有，请勿传播。</p>"
    )
    return _page("关于本书", "\n".join(body))

def render_epub(data: DocData, output_path: Path) -> Path:
    """把 DocData 渲染为 EPUB 3 电子书，返回产物路径。"""
    pages: list[tuple[str, str, str, str]] = []  # (id, filename, nav 标题, 内容)

    cover_xhtml = _cover_page(data)
    cover_image = data.cover_image()
    if cover_xhtml is not None:
        pages.append(("cover-page", "cover.xhtml", "封面", cover_xhtml))
    pages.append(("titlepage", "titlepage.xhtml", "书名页", _titlepage(data)))
    pages.append(("preface", "preface.xhtml", "导读：核心要点", _preface_page(data)))

    figure_number = 1
    for chapter in data.summary.chapters:
        filename = f"chapter-{chapter.index + 1:02d}.xhtml"
        content, figure_number = _chapter_page(data, chapter, figure_number)
        pages.append(
            (
                f"chapter-{chapter.index + 1:02d}",
                filename,
                f"{chapter.index + 1}. {chapter.title}",
                content,
            )
        )
    pages.append(("appendix", "appendix.xhtml", "关于本书", _appendix_page(data)))

    # ---- nav.xhtml（EPUB3 目录 + landmarks） ----
    nav_items = "\n".join(
        f'      <li><a href="{_attr(filename)}">{xml_escape(title)}</a></li>'
        for _id, filename, title, _content in pages
    )
    landmarks: list[str] = []
    if cover_xhtml is not None:
        landmarks.append('<li><a epub:type="cover" href="cover.xhtml">封面</a></li>')
    landmarks.append('<li><a epub:type="toc" href="nav.xhtml">目录</a></li>')
    landmarks.append('<li><a epub:type="bodymatter" href="preface.xhtml">正文</a></li>')
    nav_body = (
        '<nav epub:type="toc" id="toc">\n'
        "  <h2>目录</h2>\n"
        '  <ol class="toc-list">\n'
        f"{nav_items}\n"
        "  </ol>\n"
        "</nav>\n"
        '<nav epub:type="landmarks" id="landmarks" hidden="hidden">\n'
        "  <ol>\n"
        f"    {'      '.join(landmarks)}\n"
        "  </ol>\n"
        "</nav>"
    )
    nav_xhtml = _page("目录", nav_body)

    # ---- 图片清单（封面 + 章节配图） ----
    image_names: list[str] = []
    if cover_image is not None:
        image_names.append(cover_image.name)
    for chapter in data.summary.chapters:
        for _start, _caption, path in data.chapter_figures(chapter):
            if path.name not in image_names:
                image_names.append(path.name)

    # ---- OPF（元数据 + 清单 + 书脊） ----
    uid = f"urn:vidar:{data.meta.bvid}" if data.meta.bvid else f"urn:vidar:{data.meta.title}"
    modified = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    published = (
        data.meta.pubdate.strftime("%Y-%m-%d")
        if data.meta.pubdate
        else datetime.now().strftime("%Y-%m-%d")
    )
    manifest: list[str] = [
        '    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>',
        '    <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>',
        '    <item id="css" href="style.css" media-type="text/css"/>',
    ]
    for page_id, filename, _title, _content in pages:
        manifest.append(
            f'    <item id="{page_id}" href="{_attr(filename)}" '
            'media-type="application/xhtml+xml"/>'
        )
    for name in image_names:
        is_cover = bool(cover_image and name == cover_image.name)
        properties = ' properties="cover-image"' if is_cover else ""
        image_id = "cover-image" if is_cover else f"img-{Path(name).stem}"
        manifest.append(
            f'    <item id="{image_id}" href="images/{_attr(name)}" '
            f'media-type="image/jpeg"{properties}/>'
        )
    spine = ['    <itemref idref="nav" linear="yes"/>'] + [
        f'    <itemref idref="{page_id}"/>' for page_id, _f, _t, _c in pages
    ]
    cover_meta = (
        '\n    <meta name="cover" content="cover-image"/>' if cover_image is not None else ""
    )
    opf = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" '
        'unique-identifier="pub-id" xml:lang="zh-CN">\n'
        '  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">\n'
        f'    <dc:identifier id="pub-id">{xml_escape(uid)}</dc:identifier>\n'
        f"    <dc:title>{xml_escape(data.meta.title)}</dc:title>\n"
        f'    <dc:creator>{xml_escape(data.meta.up_name or "B 站 UP 主")}</dc:creator>\n'
        "    <dc:language>zh-CN</dc:language>\n"
        "    <dc:publisher>VIDAR</dc:publisher>\n"
        f"    <dc:description>{xml_escape(data.summary.one_liner)}</dc:description>\n"
        f"    <dc:date>{published}</dc:date>\n"
        f'    <meta property="dcterms:modified">{modified}</meta>{cover_meta}\n'
        "  </metadata>\n"
        "  <manifest>\n" + "\n".join(manifest) + "\n  </manifest>\n"
        '  <spine toc="ncx">\n' + "\n".join(spine) + "\n  </spine>\n"
        "</package>\n"
    )

    # ---- toc.ncx（EPUB2 兼容目录） ----
    nav_points = "\n".join(
        f'    <navPoint id="nav-{index}" playOrder="{index}">\n'
        f"      <navLabel><text>{xml_escape(title)}</text></navLabel>\n"
        f'      <content src="{_attr(filename)}"/>\n'
        "    </navPoint>"
        for index, (_id, filename, title, _content) in enumerate(pages, start=1)
    )
    ncx = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">\n'
        "  <head>\n"
        f'    <meta name="dtb:uid" content="{_attr(uid)}"/>\n'
        '    <meta name="dtb:depth" content="1"/>\n'
        '    <meta name="dtb:totalPageCount" content="0"/>\n'
        '    <meta name="dtb:maxPageNumber" content="0"/>\n'
        "  </head>\n"
        f"  <docTitle><text>{xml_escape(data.meta.title)}</text></docTitle>\n"
        "  <navMap>\n" + nav_points + "\n  </navMap>\n"
        "</ncx>\n"
    )

    # ---- 写 ZIP（mimetype 必须未压缩且居首位） ----
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        mimetype = zipfile.ZipInfo("mimetype", date_time=(1980, 1, 1, 0, 0, 0))
        mimetype.compress_type = zipfile.ZIP_STORED
        archive.writestr(mimetype, "application/epub+zip")
        archive.writestr("META-INF/container.xml", _CONTAINER)
        archive.writestr("OEBPS/content.opf", opf)
        archive.writestr("OEBPS/nav.xhtml", nav_xhtml)
        archive.writestr("OEBPS/toc.ncx", ncx)
        archive.writestr("OEBPS/style.css", _CSS)
        for _page_id, filename, _title, content in pages:
            archive.writestr(f"OEBPS/{filename}", content)
        for name in image_names:
            source = data.images_dir / name
            if source.exists():
                archive.writestr(f"OEBPS/images/{name}", source.read_bytes())

    return output_path


__all__ = ["render_epub"]

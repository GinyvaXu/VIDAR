"""准备 PDF 所需的中文字体：Noto Serif SC（思源宋体同源，SIL OFL 授权）静态子集。

来源优先级：
1) --source 指定的字体文件
2) 本机变量字体 C:\\Windows\\Fonts\\NotoSerifSC-VF.ttf
3) 常见下载源（jsDelivr / Google Fonts，视网络可用性）

产物（提交入库，随便携包分发）：
    src/vidar/assets/fonts/NotoSerifSC-Regular.ttf
    src/vidar/assets/fonts/NotoSerifSC-Bold.ttf
子集范围：GB2312 全量汉字 + ASCII + 常用中英标点/符号。

用法：uv run python scripts/prepare_fonts.py
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

FONT_DIR = Path(__file__).resolve().parent.parent / "src" / "vidar" / "assets" / "fonts"
LOCAL_VF = Path(r"C:\Windows\Fonts\NotoSerifSC-VF.ttf")
REMOTE_VF_URLS = [
    "https://cdn.jsdelivr.net/gh/google/fonts@main/ofl/notoserifsc/NotoSerifSC%5Bwght%5D.ttf",
    "https://raw.githubusercontent.com/google/fonts/main/ofl/notoserifsc/NotoSerifSC%5Bwght%5D.ttf",
]

PUNCTUATION = (
    "、。，．！？；：…—－·‘’“”（）《》〈〉【】〔〕「」『』～％＋－×÷＝"
    "　·•™©®§¶†‡‰′″℃￥$€£¥"
)
SYMBOLS = "①②③④⑤⑥⑦⑧⑨⑩⑴⑵⑶★☆●○◆◇■□▲△→←↑↓↔⇒√×±≈≠≤≥∞∅°"


def gb2312_charset() -> set[str]:
    chars: set[str] = set()
    for lead in range(0xA1, 0xFA):
        for trail in range(0xA1, 0xFF):
            try:
                char = bytes([lead, trail]).decode("gb2312")
            except UnicodeDecodeError:
                continue
            if char:
                chars.add(char)
    return chars


def build_charset() -> str:
    chars = gb2312_charset()
    chars.update(chr(code) for code in range(0x20, 0x7F))  # ASCII
    chars.update(PUNCTUATION)
    chars.update(SYMBOLS)
    return "".join(sorted(chars))


def find_source(explicit: Path | None) -> Path:
    if explicit is not None:
        if not explicit.exists():
            raise SystemExit(f"指定的字体文件不存在：{explicit}")
        return explicit
    if LOCAL_VF.exists():
        return LOCAL_VF
    import httpx

    for url in REMOTE_VF_URLS:
        try:
            print(f"本机未找到字体，尝试下载：{url}")
            with httpx.Client(
                timeout=httpx.Timeout(30.0, read=120.0), follow_redirects=True
            ) as client:
                response = client.get(url)
                response.raise_for_status()
                target = FONT_DIR / "source-vf.ttf"
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(response.content)
                return target
        except Exception as exc:  # noqa: BLE001
            print(f"  失败：{type(exc).__name__}: {exc}")
    raise SystemExit("未找到可用字体源：请用 --source 指定 Noto Serif SC 可变字体文件")


def make_static_subset(source: Path, weight: int, output: Path, charset: str) -> None:
    from fontTools import subset
    from fontTools.ttLib import TTFont
    from fontTools.varLib import instancer

    font = TTFont(str(source))
    if "fvar" in font:
        font = instancer.instantiateVariableFont(
            font, {"wght": weight}, inplace=False, updateFontNames=True
        )
    options = subset.Options()
    options.name_IDs = ["*"]
    options.name_legacy = True
    options.name_languages = ["*"]
    options.notdef_outline = True
    options.recalc_bounds = True
    subsetter = subset.Subsetter(options=options)
    subsetter.populate(text=charset)
    subsetter.subset(font)
    output.parent.mkdir(parents=True, exist_ok=True)
    font.save(str(output))


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="生成 PDF 用的思源宋体子集")
    parser.add_argument("--source", type=Path, default=None, help="Noto Serif SC 可变字体路径")
    args = parser.parse_args()

    if sys.platform != "win32" and args.source is None:
        print("提示：非 Windows 环境需用 --source 指定字体文件")

    source = find_source(args.source)
    print(f"字体来源：{source}（{source.stat().st_size / 1048576:.1f} MB）")

    charset = build_charset()
    print(f"子集字符数：{len(charset)}")

    outputs = {
        "Regular": 400,
        "Bold": 700,
    }
    for name, weight in outputs.items():
        target = FONT_DIR / f"NotoSerifSC-{name}.ttf"
        make_static_subset(source, weight, target, charset)
        print(f"✓ {target.name}（{target.stat().st_size / 1048576:.2f} MB）")

    license_note = FONT_DIR / "README.txt"
    license_note.write_text(
        "字体：Noto Serif SC（思源宋体同源），SIL Open Font License 1.1\n"
        "https://scripts.sil.org/OFL\n"
        "由 scripts/prepare_fonts.py 从可变字体实例化并子集化（GB2312 + ASCII + 常用标点）。\n",
        encoding="utf-8",
    )
    print(f"✓ {license_note.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

# 图标来源与许可

本目录图标拷贝自本机图标素材库 `C:\AIAgentBase\Project6-图标素材库`，保持源文件名（便于回溯）。

| 文件 | 来源路径 | 许可 |
|------|----------|------|
| play / clock / cpu / settings / info / rocket / square / folder-open / eye / refresh-cw / download / gauge / languages / book-open / file-text / x / circle-check / triangle-alert / external-link / sun / moon / sparkles / box / list | `icons/lucide/core/*.svg` | ISC（无需署名） |
| loading-loop | `icons/iconify/line-md/loading-loop.svg` | MIT（无需署名） |

约定：
- 全部为 `currentColor` 单色 SVG，运行时由 `vidar/gui/theme.py` 按主题色重着色（见 `icon()`）；
- 一个项目固定 Lucide + line-md 两套，不混搭其它风格；
- 需要新图标时：用素材库 `node scripts/find-icon.mjs <关键词>` 检索 → 拷贝 → 保持文件名。

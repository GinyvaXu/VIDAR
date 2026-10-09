# VIDAR 产品化升级计划（向 v1.0.0）

> 决策记录（2026-10-09，与用户确认）
>
> | 议题 | 决定 |
> |------|------|
> | 视觉交互层 | **自绘现代风**：PySide6 + 设计 token 体系 + 自绘 QSS + 图标库（明暗主题、侧边栏导航、卡片化）；不引入重型组件库 |
> | 翻译形态 | **双语对照**：原文+译文逐段对照；MD/PDF/EPUB 可选「双语」或「只看译文」；SRT 可双语；默认英/日/韩→中 |
> | 附加功能 | **批量任务队列、多 P 合集**（本轮）；扩展视频源 / 分享长图 / Obsidian 增强 → 暂缓 |
> | 推进顺序 | **功能优先** → 视觉层统一重写 → 品牌与仓库 → v1.0.0 发布 |
> | 兼容 | CLI / 配置文件保持向后兼容（必要时提供迁移） |
> | 图标体系 | Lucide 为主 + svg-spinners 加载动画（MIT/ISC 可商用；按 icon-assets 技能流程检索/拷贝/不改名） |
> | README | 中文为主 + `README.en.md` |

## 阶段 A：功能扩展（先做）

### A1 翻译（双语对照）— ✅ 已完成（v0.7.0，2026-10-09）
- **数据链路**：ASR 语言自动检测 → refine（源语言精修）→ **translate**（逐段 1:1 译文，`translate.json`）
  → chapters / summarize 基于译文 → 导出双语
- **导出排版**：MD / PDF / EPUB / Word：译文为正文，原文以小字灰色紧随（可在配置关闭）；
  SRT 双语双行；无译文时全部行为与现状一致
- **开关**：`translate.enabled`（默认开，源语言即目标语言时自动跳过）、`translate.target_language`、
  `translate.max_chars`、`export.bilingual`
- **验收**：✅ 真实 LLM 跑通 zh→en 双语（40 段）；MD/SRT/PDF/Word/EPUB 双语排版抽查通过；中文视频跳过逻辑单测覆盖

### A2 模型下载交互（服务层先行）— ✅ 已完成（v0.8.0）
- 下载进度回调（百分比/速度/剩余）、断点续传与校验报告、镜像切换、按显存推荐档位
- CLI：`vidar model list / recommend`；服务层（model_store）为 GUI 模型管理页预留
- **验收**：✅ 真实机器检测 8GB 显存 → 推荐 large-v3；list 正确显示已安装状态

### A3 LLM 接入引导（Provider 服务）— ✅ 已完成（v0.8.0）
- Provider 预设（OpenCode Go / DeepSeek / SiliconFlow / Kimi / 智谱 / 本地 Ollama）、模型清单、`test-connection`
- CLI：`vidar llm presets / test / setup`；服务层为 GUI 首启向导预留
- **验收**：✅ 真实连通性测试通过（OpenCode Go，2359ms）；修复缺失会话头导致的 400

### A4 批量任务队列 — ✅ 已完成（v0.8.0）
- 队列服务：多链接排队、顺序执行、失败可续跑、事件流；CLI `vidar batch urls.txt`
- **验收**：✅ 真实两条视频 `--to resolve` 队列跑通（含 BOM 清单修复）

### A5 多 P 合集 — 待设计
- 检测多分 P 视频；策略：`merge` 合并为一本 / `split` 各自成文档；合集导航页（合并时）

## 阶段 B：视觉交互层重写（进行中）

### B1 设计系统 — ✅ 已完成（v0.9.0）
- 设计 token：色板（明/暗双主题）、字阶、间距、圆角、阴影、动效时长；`gui/theme.py` 单一来源
### B2 主窗口与页面 — ✅ 已完成（v0.9.0）
- 侧边栏导航：任务 / 历史 / 模型 / 关于；卡片式任务区（输入→进度→产物）；模型管理页（下载/推荐/校验）
- 批量队列页：随 A5/队列服务后置（服务层已就绪）
### B3 图标体系 — ✅ 已完成（v0.9.0）
- 按 icon-assets 流程检索（Lucide + line-md 动画），拷贝保名、运行时按主题色重着色（QSvgRenderer）
### B4 应用图标与品牌 — ✅ 应用图标完成（v0.9.0）
- 多尺寸 `.ico`/`.png`（任务栏/标题栏/exe/关于页）；社交预览图与 README 用图随 C 阶段
### B5 状态设计 — 进行中
- 空态 / 加载（动画图标）/ 错误态（人话提示 + 一键修复）：任务页与模型页已落地基础形态

## 阶段 C：品牌与远程仓库

- README 产品化重写（中文为主 + `README.en.md`）：徽章、截图/GIF、功能矩阵、安装指南、FAQ
- 仓库布置：topics、描述、社交预览图、Issue/PR 模板、Actions 徽章、Release 说明模板
- 示例产物与截图更新（docs/examples、docs/images）

## 阶段 D：v1.0.0 发布

- 全量测试 + 真实视频回归（中/英各一条）+ 便携包双份（发布包 + 本机调试包）
- Release 说明（更新日志汇总）、官网/文档站同步（可选）

## 暂缓项（用户未选，记录备查）

- 扩展视频源（YouTube 等）、分享长图、Obsidian 双链增强

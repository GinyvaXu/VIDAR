# 技术栈 — VIDAR

## 概览
| 维度 | 内容 |
|------|------|
| 语言/运行时 | Python 3.12（uv 管理依赖；可打包为独立 Windows 便携包） |
| 主要框架 | Typer + Rich（CLI）、PySide6 / Qt（桌面 GUI）、pydantic v2（数据模型与配置） |
| 数据存储 | 无数据库；文件式产物（JSON 中间态 + Markdown 成品），目录 `work/`、`output/` |
| 前端 | PySide6 桌面界面（跟随系统主题，单窗口） |
| 构建与打包 | PyInstaller onedir 便携包（内置 CUDA 运行时、ffmpeg、Qt） |
| 测试 | pytest（30 个用例）+ ruff 静态检查 |
| 外部服务 | 任意 OpenAI 兼容 LLM 端点（当前：OpenCode Go / deepseek-v4.1-flash） |
| 关键第三方库 | yt-dlp（下载）、faster-whisper + CTranslate2（ASR）、imageio-ffmpeg（转码兜底）、openai（SDK） |

## 核心功能实现

### 视频解析与音频下载
- **实现逻辑**：BV 号 / URL → yt-dlp 解析元信息（标题、UP、时长、发布时间）→ 仅下载最佳音频流 → ffmpeg 转 16kHz 单声道 WAV。
- **技术手段**：yt-dlp + ffmpeg（系统版优先、`imageio-ffmpeg` 静态版兜底）；只下音频可省约 90% 流量与下载时间。

### 语音识别（ASR）
- **实现逻辑**：faster-whisper large-v3（CUDA/float16）识别 → VAD 过滤静音段 + 关闭上下文回溯防复读 → 幻听短语黑名单过滤 → 按「40 字或 1.2 秒停顿」合并为阅读级段落，输出词级时间戳 `asr.json`。
- **技术手段**：CTranslate2 / faster-whisper + Silero VAD（onnxruntime）；Windows 自动注册 pip 安装（或便携包内置）的 cuBLAS / cuDNN DLL 搜索路径，解决 `cublas64_12.dll not found`；模型支持 HF ID 与本地目录，并对便携包内 `./models/<名字>` 做自动回退。

### 文稿精修（LLM 段级）
- **实现逻辑**：按 ~3500 字分块，块内逐段独立改写（去口水词、纠错别字、统一术语标点），要求输出与段号 1:1；缺失段落二次补修，明显被压缩的段落回退原文。
- **技术手段**：OpenAI 兼容 Chat Completions（JSON 模式 + 代码围栏容错解析 + 指数退避重试）；段落时间戳原样保留（零漂移），实现「原始稿 / 精修稿」双版本可追溯。

### 章节切分与摘要（map-reduce）
- **实现逻辑**：精修稿 → LLM 切章（标题 + 起始段号，失败时按时长机械切分兜底）→ 分块 map 提炼要点 → reduce 归纳「一句话结论 + 核心要点 + 章节小结」。
- **技术手段**：LLM 全程不接触时间数字，时间锚点由本地按段号映射；reduce 丢锚点时用 difflib 模糊匹配从 map 结果恢复，保证要点可回跳 B 站对应位置。

### Markdown 知识文档导出
- **实现逻辑**：确定性渲染 `notes.md`（frontmatter + 一句话结论 + 核心要点 + 章节导航表 + 章节小结，均带 B 站时间戳跳转）、`outline.md`（要点按时间区间归属章节）、原始 / 精修逐字稿（可选 SRT）、`meta.json`（引擎 / 模型 / token 统计）；按配置清理音视频。
- **技术手段**：纯本地模板渲染（不额外调用 LLM），frontmatter 兼容 Obsidian 等工具；输出目录 `output/日期_BV号_标题/`。

### 断点续跑与任务状态机
- **实现逻辑**：8 个步骤（resolve / download / audio / asr / refine / chapters / summarize / export）各自产物落盘并写入 `state.json`；已完成步骤自动跳过，`--from` 重置该步及后续；取消或崩溃后从断点续跑。
- **技术手段**：pydantic 序列化状态机 + 产物存在性校验；下载 / 转码 / ASR 支持协作式取消（下载进度钩子、子进程轮询、识别分段检查）。

### 图形界面（GUI）
- **实现逻辑**：单窗口完成「粘贴链接 → 开始 / 取消 → 步骤进度 + 实时日志 → 打开文稿」；历史任务双击定位未完成步骤；设置面板写回 `config.toml`。
- **技术手段**：PySide6 / Qt（QThread + 信号槽跨线程回传事件与日志，logging handler 转 UI 信号）。

### 便携版构建
- **实现逻辑**：PyInstaller onedir 打包 GUI 与 CLI 双 exe（共享运行时目录），内置 CUDA 运行时 DLL、静态 ffmpeg、Qt 库；双击 exe 即用，模型目录外置（约 3GB，可复制）。
- **技术手段**：自定义 `.spec`（双 Analysis + 单 COLLECT）、`nvidia-*` pip 包 DLL 收集、`_MEIPASS` / exe 目录多路径回退；`scripts/build_portable.ps1` 一键构建与归档。

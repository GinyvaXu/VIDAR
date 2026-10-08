# 技术栈 — VIDAR

## 概览
| 维度 | 内容 |
|------|------|
| 语言/运行时 | Python 3.12（uv 管理依赖；可打包为独立 Windows 便携包） |
| 主要框架 | Typer + Rich（CLI）、PySide6 / Qt（桌面 GUI）、pydantic v2（数据模型与配置） |
| 数据存储 | 无数据库；文件式产物（JSON 中间态 + Markdown 成品），目录 `work/`、`output/` |
| 前端 | PySide6 桌面界面（跟随系统主题，单窗口） |
| 构建与打包 | PyInstaller onedir 便携包（默认精简约 250MB；GPU 运行时按需 `gpu install`；Qt 内置） |
| 排版与导出 | ReportLab（PDF 报刊/论文双模板）、python-docx（Word）、Pillow、PyAV（抽帧） |
| 测试 | pytest（30 个用例）+ ruff 静态检查 |
| 外部服务 | 任意 OpenAI 兼容 LLM 端点（当前：OpenCode Go / deepseek-v4.1-flash） |
| 关键第三方库 | yt-dlp（下载）、faster-whisper + CTranslate2（ASR）、imageio-ffmpeg（转码兜底）、openai（SDK） |

## 核心功能实现

### 视频解析与音频下载
- **实现逻辑**：BV 号 / URL → yt-dlp 解析元信息（标题、UP、时长、发布时间）→ 仅下载最佳音频流 → ffmpeg 转 16kHz 单声道 WAV。
- **技术手段**：yt-dlp + ffmpeg（系统版优先、`imageio-ffmpeg` 静态版兜底）；只下音频可省约 90% 流量与下载时间。

### 音频解码（PyAV）
- **实现逻辑**：把下载到的音频（m4a 等）解码为 16kHz 单声道 16-bit PCM WAV，作为 ASR 输入。
- **技术手段**：faster-whisper 内置的 `decode_audio`（PyAV 底层解码）+ 标准库 `wave` 写盘；不依赖外部 ffmpeg（ffmpeg 降级为可选组件，仅用于探测等扩展能力）。

### 语音识别（ASR）
- **实现逻辑**：faster-whisper large-v3（CUDA/float16）识别 → VAD 过滤静音段 + 关闭上下文回溯防复读 → 幻听短语黑名单过滤 → 按「40 字或 1.2 秒停顿」合并为阅读级段落，输出词级时间戳 `asr.json`。
- **技术手段**：CTranslate2 / faster-whisper + Silero VAD（onnxruntime）；Windows 自动注册 pip 安装（或便携包内置）的 cuBLAS / cuDNN DLL 搜索路径，解决 `cublas64_12.dll not found`；便携包可运行 `vidar gpu install` 从 PyPI 镜像按需安装（默认只提取实测最小集，约 1.5GB 磁盘）；模型支持 HF ID 与本地目录，并对便携包内 `./models/<名字>` 做自动回退。

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
- **实现逻辑**：9 个步骤（resolve / download / audio / asr / refine / chapters / summarize / frames / export）各自产物落盘并写入 `state.json`；已完成步骤自动跳过，`--from` 重置该步及后续；取消或崩溃后从断点续跑。
- **技术手段**：pydantic 序列化状态机 + 产物存在性校验；下载 / 转码 / ASR 支持协作式取消（下载进度钩子、子进程轮询、识别分段检查）。

### 图形界面（GUI）
- **实现逻辑**：单窗口完成「粘贴链接 → 开始 / 取消 → 步骤进度 + 实时日志 → 打开文稿」；历史任务双击定位未完成步骤；设置面板写回 `config.toml`。
- **技术手段**：PySide6 / Qt（QThread + 信号槽跨线程回传事件与日志，logging handler 转 UI 信号）。

### 关键帧与要点截图（frames 步骤）
- **实现逻辑**：两路截图——① 章节配图：summarize 阶段由 LLM 逐章输出「值得配图时刻」（anchor 段号 + ≤20 字图注，0-2 项/章）；
  ② 要点截图：每个核心要点按时间戳直接抽一帧（不依赖 LLM 判断），报刊「本期看点」左图右文，渲染期按密度档位筛选；
  frames 步骤下载 720p 视频流后按锚点抽帧：dHash 指纹去重、相邻配图最小间隔过滤，图片落盘 `images/` 后立即删除视频文件。
- **技术手段**：yt-dlp 仅视频轨下载；PyAV 按时间点 seek + decode 取帧；Pillow 编码 JPEG；
  LLM 全程只接触段号，时间戳由本地映射（与摘要锚点同一套机制，兼容区间/字符串锚点，缺失时按相邻要点插值兜底）。

### Word 导出与 PDF 排版
- **实现逻辑**：导出阶段可选生成 `Word版-标题.docx`（标题/摘要/要点/章节金句/配图/全文，含时间戳）与
  `报刊版-标题.pdf` / `论文版-标题.pdf`（两种 A4 打印版式：报刊风=双栏+报头+看点框（含要点缩略图）+金句；
  论文风=标题区+摘要+目录+编号章节+尾注）。正文档位默认全文（精修稿按章节分组），`brief` 档只出摘要与章节小结；
  字号档位 `pdf_scale`（0.85/1.0/1.18 或任意系数）缩放全部字号与间距；输出文件统一 `<变体>版-<标题>` 命名。
- **技术手段**：python-docx（eastAsia 字体设定，宋体正文/微软雅黑标题）；ReportLab Platypus
  （多栏 Frame、TableOfContents 两遍渲染 + bookmarkPage、canvas 页眉页脚自适应缩放、表格跨栏分页、图注 flowable）；
  内置思源宋体子集（OFL，构建期 fonttools 实例化+GB2312 子集化，PDF 仅嵌入实际用字）。

### 排版快速调试（预览窗口 / 独立重渲染）
- **实现逻辑**：`vidar pdf <BV>` 与 GUI「预览排版…」共享同一重渲染通道：直接读 `work/<key>/` 已有产物组装
  DocData（不触发任何 LLM/ASR），改风格/篇幅/字号/截图密度后数秒重出 PDF；GUI 内嵌 PDF 预览，随改随看。
- **技术手段**：`vidar/rerender.py` 复用 PDF 渲染入口；PySide6 QtPdf（QPdfDocument + QPdfView）做内嵌预览，
  渲染放 QThread 防卡 UI；PyInstaller 打包时补充 QtPdf/QtPdfWidgets hiddenimports。

### EPUB 电子书导出（微信读书 / 墨水屏）
- **实现逻辑**：`render_epub` 把同一份 DocData 输出为 EPUB 3：封面 → 书名页 → 目录 → 导读（一句话+要点+时间跳转）
  → 各章（小结/金句/章节配图/全文）→ 尾页；中文只出现在可见正文，**书内文件名/属性值/CSS 全 ASCII**
  （社区实测「属性带中文」会导致微信读书/Apple Books 图片不显示）；附 `电子书版-标题.epub`。
- **技术手段**：标准 EPUB 3（mimetype 未压缩居首 + container.xml + OPF + nav.xhtml）并附 toc.ncx 兼容旧阅读器；
  纯标准库 zipfile 手写打包（零新增依赖）；重排排版 CSS（相对字号/首行缩进/图片 max-width:100%，不内嵌字体）；
  列表项/属性值统一 HTML 转义防破坏 XML；测试含「包内文件名全 ASCII」等兼容守卫。

### 便携版构建与 GPU 按需安装
- **实现逻辑**：PyInstaller onedir 打包 GUI 与 CLI 双 exe（共享运行时目录）。默认**精简包**（约 250MB，不含 CUDA，音频解码用 PyAV 无需 ffmpeg）；N 卡用户运行 `vidar gpu install` 从 PyPI 镜像安装 cuBLAS/cuDNN 实测最小集；`build_portable.ps1 -WithCuda` 可构建内置 CUDA 的完整版。模型目录外置（约 3GB，可复制）。
- **技术手段**：自定义 `.spec`（双 Analysis + 单 COLLECT，`VIDAR_BUNDLE_CUDA` 开关）；从 nvidia wheel（zip）按白名单提取 DLL（剔除 adv/nvrtc 等非必需项，省约 474MB）；`_MEIPASS` / exe 目录多路径回退；`scripts/build_portable.ps1` 一键构建与归档。

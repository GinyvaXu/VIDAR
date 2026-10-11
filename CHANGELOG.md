# 更新日志

## [0.12.0] - 2026-10-11
### Added
- **批量队列页（GUI）**：「队列」页粘贴多行链接（或从文件导入，自动去重）→ 顺序处理、失败不中断；
  每条实时显示当前步骤与进度，支持「停止 / 重试失败项 / 移除所选 / 清空列表」；与任务页互斥不并发
- **LLM 接入向导**：首次启动检测到未配置 LLM 时自动弹出——选服务商预设（6 家）/自定义 → 填 Key →
  一键连通性测试（附人话修复建议）→ 保存；环境变量已提供 Key 时提示可留空（不落盘）；
  关于页新增入口可随时重跑；设置面板新增「测试连接」（不离开对话框验证配置）
- 配置项 `[ui].llm_wizard_done`：向导完成标记（避免重复打扰，可手动改回 false 重新触发）

### Fixed
- 队列页状态标签：加入条目后从「队列为空」更新为「共 N 条 · 待处理 N」（截图验收发现）

### Changed
- `batch.run_batch` 新增 `on_ctx` 回调（GUI 队列「停止」立即取消当前条目；行为与语义不变）
- 侧边栏扩为五页（任务 / 队列 / 历史 / 模型 / 关于）；GUI 冒烟脚本覆盖队列页与两个对话框

## [0.11.1] - 2026-10-10
### Fixed
- **本机调试包升级丢历史**：本机包改为与主项目**共享工作/输出/日志目录**（config.toml 写绝对路径），
  历史任务、产物、检查点跨版本保留，升级不再"断档"
- 已把散落在旧本机包（v0.9.0 / v0.10.0）的任务数据合并回主项目目录：
  恢复案例 `BV1gB4y147jf`（给未来孩子的信：不要围观他人的生活）——补跑语段编辑（69 段）并重新导出全套产物

## [0.11.0] - 2026-10-10
### Added
- **语段编辑（LLM）**：新增 `paragraphs` 步骤——把零散句子组织成自然段（只决定边界、不改写文字）；
  真实视频实测：288 句 → 82 段（平均 175 字，全部落在 60–260 字区间）
- **首行缩进两格**：PDF（报刊/论文）、Word、EPUB 正文段落统一空两格
- 配置 `[paragraph]`：`enabled` / `min_chars`（过短自动并入相邻段）/ `max_chars`（过长均衡拆分）
- 边界兜底：LLM 分组非法时自动追加一次严格请求，仍失败则按长度机械编组；过短合并 + 均衡拆分保证「不太长也不太短」

### Changed
- 流水线扩为 11 步（`summarize → paragraphs → frames → export`）
- 旧任务（无 paragraphs.json）渲染自动回退逐句排版，向后兼容

## [0.10.0] - 2026-10-10
### Added
- **暂停 / 继续 / 释放显存**（全流水线）：ASR 段间、LLM 各步请求块间、抽帧每帧间均可挂起；
  任务页新增「暂停 / 继续 / 释放显存」按钮
- **检查点续跑**：暂停释放时各步骤把进度写入 `*.partial.json`，继续时跳过已完成部分——
  ASR 按结束时间点**切片音频续识别**（不从头再来），refine / translate / summarize 按块续跑
- **历史页进度记忆**：列表项带进度徽标（完成 ✓ / 已暂停 ⏸ / 失败 ⚠ / x/10 步）；
  选中任务展开**步骤明细**，可「继续（自动定位未完成）」或选中任意步骤「从此步重跑」（带影响范围确认）
- 「释放显存」后运行干净结束（当前步骤标记为可续跑），GUI 重启后仍可一键继续

### Changed
- `PipelineRunner.run` 返回运行状态（finished / paused）；暂停释放不再算失败
- 下载环节维持「取消 + 重跑续传」语义（yt-dlp 断点续传），不提供挂起

## [0.9.0] - 2026-10-09
### Added
- **视觉交互层重写（B1-B4）**：侧边栏导航（任务 / 历史 / 模型 / 关于）+ 卡片式布局 + 明暗双主题
  （`[ui].theme = auto|light|dark`，侧边栏一键切换并持久化）
- **设计系统**：`gui/theme.py` 统一 token（色板 / 圆角 / 间距 / 字阶）+ 全局 QSS（对话框一并生效）
- **图标体系**：按本机图标素材库流程引入 Lucide（ISC）+ line-md（MIT）25 枚，`currentColor` 运行时按主题重着色
- **应用图标**：自绘 SVG（播放三角 + 文档线）→ `vidar.ico` 多尺寸（exe / 任务栏 / 窗口 / 关于页），
  `scripts/make_app_icon.py` 一键重生成
- **模型管理页（GUI）**：显存推荐横幅、模型表格（安装状态）、下载（实时进度/速度）、取消、校验

### Changed
- 主窗口信息架构重排：历史/操作按钮独立成页；设置改为侧边栏入口；日志/步骤卡片化
- 便携包 exe 带应用图标（spec `icon=`）；新增 `PySide6.QtSvg` 打包依赖

## [0.8.0] - 2026-10-09
### Added
- **模型下载交互（A2）**：`vidar model list`（型号 / 大小 / 显存建议 / 安装状态）、
  `vidar model recommend`（读取 nvidia-smi 显存自动推荐档位）；服务层暴露进度回调与断点续传（供 GUI 模型管理页复用）
- **LLM 接入引导（A3）**：内置 6 家服务商预设（OpenCode Go / DeepSeek / 硅基流动 / Kimi / 智谱 / 本地 Ollama）；
  `vidar llm presets` 列出预设与 Key 获取方式；`vidar llm test` 真实连通性测试（1 token，含人话修复建议）；
  `vidar llm setup <预设> [--key] --write` 一键写入配置（环境变量已提供 Key 时不再落盘）
- **批量任务队列（A4）**：`vidar batch <清单文件>` 顺序处理多条视频、失败不中断（`--stop-on-error` 可选）；
  事件流（batch_start / batch_item / batch_finished）为 GUI 队列页预留

### Fixed
- `vidar llm test` 缺少 OpenCode Go 会话头导致 400：统一请求头构造（客户端与测试共用）
- 批量清单兼容 Windows BOM（utf-8-sig），避免首行被当成不同来源
- rich 标记转义：错误建议中的 `[llm]` 不再被当作样式标记吞掉

## [0.7.0] - 2026-10-09
### Added
- **双语翻译（外语视频自动翻译）**：ASR 语言自动检测（`language = "auto"` 为默认）；新增 `translate` 流水线步骤，
  逐段 1:1 翻译（`translate.json`），缺段自动重试、异常回退原文；章节与摘要基于译文生成，保证文档语言一致
- **双语排版**：精修稿（译文在上、原文在下对照）；SRT 双语（可 `bilingual=false` 只出译文）；
  PDF / EPUB / Word（译文正文 + 原文小字）；`--no-bilingual` 可只出译文
- 翻译配置 `[translate]`：`enabled` / `target_language`（zh/en/ja/ko…）/ `max_chars`；
  CLI：`run --translate/--no-translate --target-lang <lang> --bilingual/--no-bilingual`，`vidar pdf/epub --bilingual/--no-bilingual`
- GUI：设置面板新增「自动翻译外语视频 / 目标语言 / 双语排版」；预览排版窗口新增「双语对照」勾选

### Changed
- 流水线扩为 10 步：`refine → translate → chapters → summarize`（中文视频自动跳过翻译）
- 精修 / 章节 / 摘要提示词改为语言无关：外语文稿保持原语言，摘要与章节标题跟随文稿语言
- m2 集成测试改为确定性（关闭截图抽帧，不再依赖真实网络下载视频）

### Fixed
- 关闭双语时正文回退到原文的语义问题：现在始终使用译文（文档语言 = 目标语言），双语开关只控制是否附原文

## [0.6.0] - 2026-10-08
### Added
- **EPUB 电子书导出**（`--epub` / `vidar epub`）：封面 → 书名页 → 目录 → 导读（要点+时间跳转）→ 各章
  （小结/金句/章节配图/全文）→ 尾页；重排排版、不内嵌字体，可直接导入**微信读书** / 电子墨水阅读器
- 成品名 `电子书版-标题.epub`；GUI 设置面板与「预览排版…」窗口新增 EPUB 选项（渲染窗口含「打开电子书」按钮）

### Changed
- EPUB 书内结构全 ASCII（文件名/属性值/CSS），规避社区已知的「属性带中文 → 图片不显示」解析问题；
  标准 EPUB 3（nav + ncx 双目录、封面声明、OPF 元数据）

### Notes
- 学自社区经验：微信读书导入 EPUB 的图片显示问题多由非标准属性引起；本实现以纯标准库手写打包，零新增依赖

## [0.5.0] - 2026-10-08
### Added
- **要点截图**：每个核心要点自动抽取一帧（不依赖 LLM 判断），报刊「本期看点」变为左图右文；
  密度档位 `point_figures = all | sparse | off`（渲染期切换，无需重跑）
- **字号档位** `pdf_scale = compact | standard | large`（0.85 / 1.0 / 1.18，也支持 0.6–1.5 数字）：小字一页放更多内容
- **排版预览窗口**（GUI「预览排版…」）：内嵌 PDF 预览（QtPdf）+ 风格/篇幅/字号/截图密度面板，改完一键重渲染
- **独立重渲染命令** `vidar pdf <BV>`：不重跑流水线，几秒重出 PDF（`--style` / `--mode` / `--scale` / `--point-figures` / `--open`）
- 要点锚点本地兜底：兼容区间/字符串/浮点锚点格式（如 `"71-85"` 取 71）；仍有缺失时按相邻要点插值，
  保证每个要点都有截图；`VIDAR_DEBUG_LLM=1` 落盘 map/reduce 原始返回便于排查

### Changed
- **输出文件统一命名**：`<变体>版-<视频标题>.<ext>`（如 `报刊版-标题.pdf`、`论文版-标题.pdf`、`笔记版-标题.md`、`Word版-标题.docx`）
- 页眉标题改为自适应（自动缩字号，仍放不下才截断），长标题不再显示不全
- 报刊「本期看点」支持缩略图（`point_thumb_mm` 可调宽度），表格可跨栏分页
- 抽帧产物每次重建（清理旧图），输出目录同步清理过期截图
- **本机调试包**：构建时自动额外产出一份（模型路径 / API / 导出开关预配置），稳定入口 `local\VIDAR.exe`
- GPU 运行时支持**机器级共享目录**（`vidar gpu install --shared`）：多版本便携包共用一份，免每版复制约 1.2GB

## [0.4.0] - 2026-10-08
### Added
- **关键帧配图**：LLM 逐章判断值得配图的时刻（anchor 段号 + 图注），720p 抽帧、dHash 去重、抽完即删视频
- **PDF 导出**（`--pdf`）：报刊风（双栏/报头/看点框/金句）与论文风（标题区/摘要/目录/编号章节），
  内置思源宋体子集、A4 打印友好、图注含时间戳；`--pdf-style` / `--pdf-mode` 切换
- **Word 导出**（`--docx`）：图文混排、可再编辑
- 章节金句（quote）由 reduce 阶段顺带产出；配图锚点被模型偶发省略时自动追加一次请求
- GUI 设置面板新增：截图 / PDF / Word 开关与 PDF 风格、篇幅选择
- `scripts/preview_pdf.py` 排版预览工具（PDF 逐页渲染 PNG）；`scripts/prepare_fonts.py` 字体准备

### Changed
- 流水线新增第 9 步 `frames`（关键帧截图）；export 产物新增 `notes.pdf` / `notes.docx` / `images/`
- 依赖新增（核心）：`reportlab`、`pillow`、`python-docx`、`av`

## [0.3.1] - 2026-09-29
### Added
- `vidar gpu install`：从 PyPI 镜像一键安装 GPU 运行时（cuBLAS/cuDNN 实测最小集，约 1.2GB 下载 / 1.5GB 磁盘）
- doctor：无 CUDA 时提示 `vidar gpu install`；ffmpeg 改为可选（缺失仅警告）

### Changed
- 便携版主包精简：移除内置 CUDA 运行时（2.0GB）与静态 ffmpeg（84MB），zip 从 1.47GB 降至约 0.25GB
  （需要内置 CUDA 的完整版仍可用 `scripts/build_portable.ps1 -WithCuda` 构建）
- 音频解码改用 PyAV（faster-whisper 自带），不再依赖外部 ffmpeg

## [0.3.0] - 2026-09-29
### Added
- `vidar model download` / `vidar model check`：ASR 模型辅助下载（分片并行、断点续传、大小校验，默认 hf-mirror 源）
- doctor 的本地模型检查升级为 5 个文件逐一比对
- GitHub Actions CI（ruff + pytest，Ubuntu / Windows 双平台）
- MIT LICENSE、示例产物（docs/examples/）、公开 README

### Changed
- 项目更名为 **VIDAR**（原 BiliVideoKing）：包名 `biliking` → `vidar`，命令 `vidar` / `vidar-gui`
  （保留 `biliking` 旧命令别名与 `BILIKING_*` 环境变量、旧配置目录兼容）
- 版本号继续以根目录 `VERSION` 为唯一来源

## [0.2.0] - 2026-09-28
### Added
- 语音识别：faster-whisper large-v3（本地模型 / CUDA），VAD + 幻听黑名单过滤，按阅读节奏合并段落，产出词级时间戳
- 文稿精修：LLM 段级改写（去口水词 / 纠错 / 术语规范），段号与时间戳 1:1 保留，缺段自动补修、异常回退原文
- 章节切分与摘要：map-reduce 生成一句话结论 / 核心要点 / 章节小结，要点带 B 站时间戳跳转链接，失败自动机械切分兜底
- Markdown 知识文档导出：notes.md / outline.md / 原始逐字稿 / 精修稿 / meta.json（含 token 统计），可选 SRT
- 图形界面（PySide6）：步骤进度、实时日志、历史任务续跑、设置面板、开始/取消
- 断点续跑：8 步状态机（state.json），`--from` 指定重跑步骤，已完成步骤自动跳过
- 便携版打包：PyInstaller onedir（GUI + CLI 双 exe，内置 CUDA 运行时与 ffmpeg），双击即用
- LLM 接入 OpenCode Go（deepseek-v4.1-flash），兼容任意 OpenAI 兼容端点
- doctor 环境自检（依赖 / GPU / 模型 / API 连通性）与配置三级优先级（CLI > 环境变量 > TOML）

### Fixed
- Windows 下 CTranslate2 报 `cublas64_12.dll is not found`：自动注册 CUDA DLL 搜索路径（PATH + add_dll_directory）
- 章节小结复读标题、占位话术（"未提供 / 未展开"）过滤
- 精修阶段个别段落缺失时自动补修，不再降级为原文

### Changed
- 版本号改为读取根目录 `VERSION` 文件（单一来源，禁止源码手写）
- ffmpeg 免手动安装：imageio-ffmpeg 静态版兜底（系统 ffmpeg 优先）
- 下载仅取音频流；处理完成后默认清理音视频（`--keep-media` / `--keep-audio` 可保留）

## [0.1.0] - 2026-08-12
### Added
- 项目初始化（由 ProjectDock 预设生成）

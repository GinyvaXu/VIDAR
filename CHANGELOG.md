# 更新日志

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

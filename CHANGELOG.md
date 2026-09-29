# 更新日志

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

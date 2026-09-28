# BiliVideoKing

把 B 站视频变成可读、可检索、可追溯的 Markdown 知识文档。

```
BV链接 → 解析 → 下载(仅音频) → 语音识别 → 原始逐字稿 → LLM 精修稿
      → 分章节目录 → 结构化摘要 → Markdown 大纲 → 知识库导出
```

## 当前状态

| 里程碑 | 内容 | 状态 |
|--------|------|------|
| M0 | CLI / 配置 / doctor 自检 / 状态机 / 文本工具 / 单元测试 | ✅ |
| M1 | 解析 + 下载 + 音频转码 + ASR + 原始逐字稿 | 🚧 进行中 |
| M2 | 精修 + 章节切分 + 摘要 + 大纲 + 导出 | ⏳ |
| M3 | 导出增强 + 清理策略 + 费用统计 | ⏳ |

## 快速开始

前置：Windows + NVIDIA GPU（推荐）、[uv](https://docs.astral.sh/uv/)。

> ffmpeg 无需手动安装：`uv sync --extra asr` 会附带 `imageio-ffmpeg` 提供的静态 ffmpeg
> （国内网络从 GitHub 直装 ffmpeg 通常很慢，这是刻意设计）。有系统 ffmpeg 时优先使用系统版。

```powershell
# 1. 安装依赖（含 ASR 可选组）
uv sync --extra asr

# 2. 环境自检
uv run biliking doctor          # 本地检查
uv run biliking doctor --net    # 额外检查 B 站与 LLM API 连通性

# 3. 初始化配置（写入用户配置目录）
uv run biliking config init
uv run biliking config show

# 4. 试跑（查看执行计划，不做实际下载）
uv run biliking run "https://www.bilibili.com/video/BVxxxxxxxxxx" --dry-run
```

## 配置

三层优先级：**CLI 参数 > 环境变量 > config.toml > 默认值**。

- 配置文件位置：`biliking config init` 写入用户配置目录（Windows 为 `%APPDATA%\biliking\config.toml`）；也支持当前目录 `config.toml` 或 `--config <path>` 指定。
- 完整示例见 `config.example.toml`。
- 常用环境变量：
  - `BILIKING_LLM_API_KEY`（也兼容 `OPENAI_API_KEY`）
  - `BILIKING_LLM_API_BASE`（也兼容 `OPENAI_BASE_URL`），如 `https://api.deepseek.com/v1`、`https://opencode.ai/zen/v1`
  - `BILIKING_LLM_MODEL`，如 `deepseek-chat`
  - 嵌套变量用双下划线：`BILIKING_ASR__MODEL=large-v3`

## 命令

```powershell
biliking run <BV号或URL> [--dry-run] [--out DIR] [--asr-model large-v3]
                         [--device auto|cuda|cpu] [--keep-media] [--keep-audio]
biliking doctor [--net]
biliking config init [--force] / config show
biliking version
```

## 目录结构

```
work/<BV号>/      # 中间产物（asr.json 等，可续跑）
output/<日期>_<BV号>_<标题>/   # notes.md / transcript.raw.md / transcript.refined.md / outline.md / meta.json
logs/             # 运行日志
```

详见 `docs/architecture.md`。

## 合规声明

本项目仅用于个人学习与研究：不绕过付费内容、不高频批量抓取、不提供公开发布能力。
使用前请遵守 B 站用户协议与相关版权法律，下载的内容请勿传播。

## 许可证

MIT（自研实现，仅参考同类开源项目的设计思想）。

# VIDAR 需求规格 v0.1

> 一句话定位：**把 B 站视频变成可读、可检索、可追溯的知识文档（文稿 + 摘要 + 章节 + 大纲）的命令行工具。**
> 参考项目：VideoCaptioner（仅借鉴设计思路，不复制其代码，避免 GPL-3.0 传染）。

- 文档状态：待评审
- 日期：2026-09-28
- 决策依据：两轮需求问答（见文末「决策记录」）

---

## 1. 背景与目标

### 1.1 要解决的问题

B 站存在大量高质量长视频（技术分享、课程、访谈、测评）。它们的消费痛点是：

1. 视频形式**不可检索、不可跳读**，80% 的时间在听 20% 的有效信息；
2. 无法做笔记归档，看完即忘；
3. 没有官方文字稿，或官方 AI 字幕质量不可控。

### 1.2 产品目标

输入一条 BV 号 / 视频 URL，输出一套**结构化知识文档**：

```
视频 → 音频 → 语音识别 → 原始逐字稿 → LLM 精修稿
     → 结构化摘要（结论 + 要点）→ 分章节目录（带时间戳）
     → Markdown 大纲 → 导出为通用 Markdown 知识库
```

### 1.3 非目标（本期明确不做）

- 不做 GUI / Web 界面（CLI 优先，跑通后再考虑封装）
- 不做字幕烧录、视频翻译、字幕文件生产（这是 VideoCaptioner 的定位，不是我们的）
- 不做批量队列、UP 主订阅、收藏夹导入（架构上预留，本期不实现）
- 不做说话人分离（中文单人/讲解类视频为主）
- 不做 RAG 问答、双语翻译（未选入本期产出）

---

## 2. 已确认的需求基线

| # | 维度 | 决策 |
|---|------|------|
| 1 | 产品形态 | CLI 流水线优先（可脚本化、可组合） |
| 2 | 输入 | 单条 BV 号 / URL |
| 3 | ASR | 本地模型为主（Windows + NVIDIA GPU），引擎可插拔，预留免费在线引擎 |
| 4 | 产出 | 结构化摘要、分章节目录、原始逐字稿 + 精修稿、Markdown 大纲、知识库导出 |
| 5 | 技术路线 | 自研（Python），参考 VideoCaptioner 的设计 |
| 6 | 运行环境 | Windows 本机 + N 卡 |
| 7 | LLM | 用户自接 OpenAI 兼容 API（DeepSeek / opencode-go） |
| 8 | 语言 | 中文为主；不区分说话人 |
| 9 | 字幕来源 | 一律自跑 ASR，不抓 B 站平台字幕 |
| 10 | 文稿版本 | 原始逐字稿（带时间戳、保原话）＋ LLM 精修稿，两份都留 |
| 11 | 知识库形态 | 通用 Markdown 文件夹（带 frontmatter），不绑定 Obsidian/Notion |
| 12 | 大纲格式 | Markdown 缩进大纲（纯文本，可被任意工具导入） |
| 13 | 媒体文件 | 处理完删除音视频（提供 `--keep-media` 覆盖开关） |

---

## 3. 功能需求（MoSCoW）

### P0 / Must（MVP 必须）

#### FR-1 CLI 入口与配置
- 单命令完成全流程：`vidar run <BV号|URL>`
- 支持环境变量与配置文件：`config.toml` + `VIDAR_*` 环境变量
- 优先级：命令行参数 > 环境变量 > 配置文件 > 默认值
- 提供 `vidar doctor`：自检 ffmpeg / GPU / 模型文件 / API Key / 输出目录

#### FR-2 解析与下载
- 输入 BV 号或完整 URL（含分 P 指定，如 `?p=2`），解析出 `bvid / cid / 标题 / UP 主 / 时长 / 发布时间`
- **只下载音频流**（`yt-dlp -f bestaudio`）：因为媒体处理完即删，省带宽、省磁盘、省时间
- 可选 Cookie 支持（cookies.txt 路径配置项），用于高清晰度/风控场景（音频一般不需要，保留能力）
- ffmpeg 转 16kHz 单声道 WAV 供 ASR 使用
- 失败重试与明确错误提示（链接失效 / 视频不存在 / 需要登录）

#### FR-3 语音识别（ASR）
- 引擎抽象接口 `AsrEngine`，MVP 实现：**faster-whisper（CUDA）**
- 默认模型 `large-v3`；中文优化参数：
  - VAD 过滤开启（防静音段幻觉）
  - `condition_on_previous_text=False`（防重复/复读）
  - `language="zh"`，复用简中初始提示词
  - 词级时间戳输出
- 幻觉黑名单过滤（如「请点赞投币」「字幕由…提供」等高频幻听短语）
- 输出结构化 `asr.json`：段落列表（start / end / text / words）
- 预留第二引擎插槽（候选：FunASR SenseVoice-Small，本地免费且中文更快更强；在线免费接口作为备选，不承诺稳定性）

#### FR-4 文稿生成
- **原始逐字稿**（`transcript.raw.md`）：
  - 基于词级时间戳 + 标点做自然断句，每段带 `[mm:ss]` 时间戳
  - 保留口语原貌（不改写、不删词），保证可追溯
- **精修稿**（`transcript.refined.md`）：
  - 以「段」为单位调用 LLM（不做词级重排，保证时间戳可靠）：去口水词、修正同音错别字、统一专业术语、整理标点
  - 精修不改原意、不添加原文没有的信息（提示词硬约束）
  - 保留段落时间戳映射；术语表支持（P1）

#### FR-5 分章节目录
- 基于精修稿段落 + 时间轴，由 LLM 输出章节列表：章节标题 + 起始时间 +（可选）一句话概括
- 章节数量与颗粒度规则：长视频每 3–10 分钟一个章节，短视频 3–6 章
- 每章生成 B 站可点击跳转链接：`https://www.bilibili.com/video/<BV>?t=<秒>`
- 章节时间戳误差目标 ≤ 3 秒（对齐到最近的原始段落边界）

#### FR-6 结构化摘要
- 长视频必须走 **map-reduce**：
  - map：按 ~3000–4000 字切块，逐块提炼要点（带时间锚点）
  - reduce：合并去重，产出「一句话结论 + 5–10 条核心要点 + 每章小结」
- 摘要必须**可追溯**：每条要点/章节小结标注来源时间段
- 严禁编造：提示词约束「只依据文稿内容，不确定就省略」；输出与原文做一次自检比对

#### FR-7 Markdown 大纲
- 层级大纲：主题 → 章节 → 子要点（纯 Markdown 缩进列表）
- 与章节时间戳对应，可整体复制进任意笔记软件

#### FR-8 知识库导出
- 每视频一个文件夹，通用 Markdown + frontmatter
- 文件命名与目录结构稳定、可排序、无非法字符（见 §5 输出规范）

#### FR-9 收尾与清理
- 默认删除中间媒体文件（音频/WAV），保留 `asr.json` 等可复用中间结果
- `--keep-media` 保留音视频；`--keep-audio` 只保留音频（便于重跑 ASR）

#### FR-10 断点续跑
- 每个处理步骤落盘留痕（`state.json`：download → audio → asr → refine → summarize → export）
- 中断后重跑同一条命令：已完成步骤直接复用，从失败点继续
- 全部步骤带终端进度显示与结构化日志（`logs/`）

### P1 / Should（一期后半段）

- 术语表配置：用户提供「错误写法 → 正确写法」对照，注入精修提示词
- 在线免费 ASR 引擎适配器（作为本地模型不可用时的兜底）
- Token / 费用统计：每条视频各步骤 tokens 与估算成本写入 `meta.json`
- `--asr-model small|medium|large-v3` 切换，显卡不足时降级可用
- 顺手产出 `transcript.srt`（几乎零成本，方便配套播放器校对）

### P2 / Could（二期候选）

- 批量队列（多条链接 / 文件清单）
- UP 主主页、合集、收藏夹导入
- 说话人分离（多人访谈场景）
- 双语翻译、RAG 追问问答
- Obsidian / Notion 导出适配器
- 本地 LLM（Ollama）支持

### 明确不做（本期）

- Web 界面（桌面 GUI 已按需求补充，见 FR-11）、订阅监控、视频烧字幕、公开发布能力

---

## 3.1 增补需求（第二轮确认）

#### FR-11 图形界面（GUI）[已实现]
- 技术栈：PySide6 桌面窗口；主题跟随系统
- 功能范围：URL 输入 + 开始/取消；实时日志面板；8 步进度可视化；打开输出目录/文稿；
  历史任务列表（双击载入并定位未完成步骤）；设置面板（LLM/ASR/目录，写回 config.toml）；
  步骤重跑入口（起始步骤下拉）
- 任务模式：一次一条；不打包 exe（用 `uv run` / `start-gui.bat` 启动）
- 实现：`src/vidar/gui/`，共享同一套 pipeline 与状态机（与 CLI 等价，产物互通）

#### 增补的工程决策
- ASR 默认模型定为 `large-v3`（用户确认）
- LLM 采用 OpenCode Go（`https://opencode.ai/zen/go/v1`）+ `deepseek-v4.1-flash`，Key 走环境变量 `OPENCODE_GO_API_KEY`，不落盘
- 模型分发：本项目提供 hf-mirror 手动下载指引 + curl 分片脚本（HF 客户端 xet 传输在国内易卡死）
- Windows GPU 支持：随 `asr` 可选组安装 cuBLAS/cuDNN pip 包，运行时自动注册 DLL 搜索路径
- ffmpeg 免手动安装：`imageio-ffmpeg` 静态版兜底（系统 ffmpeg 优先）
- 真实视频验收（23 分钟口播）：全流程 ≈11 分钟，LLM 消耗约 1.3 万输入 / 6.6 万输出 tokens

---

## 4. 非功能需求

| 类别 | 要求 |
|------|------|
| 性能 | 20 分钟中文视频，RTX 3060 级显卡：ASR ≤ 5 分钟，LLM 环节 ≤ 5 分钟，端到端 ≤ 10 分钟（不含下载） |
| 成本 | 1 小时视频（约 1.5–2 万字）LLM 全流程成本目标 ≤ ¥1（DeepSeek 级别模型，实际以服务商计费为准） |
| 可靠性 | 网络/LLM 调用自动重试（指数退避）；单步失败不污染已完成产物 |
| 可续跑 | 任意步骤中断后重跑只补缺失步骤 |
| 可观测 | 终端进度（rich）+ 落盘日志；`meta.json` 记录引擎、模型、耗时、tokens |
| 合规 | 仅限个人学习研究使用；不绕过付费/大会员内容；不高频批量爬取；文档中明示版权声明 |
| 可移植 | Windows 优先，路径处理兼容 Linux/macOS（不写死盘符） |
| 许可证 | 自研代码选宽松许可（建议 MIT）；不引入 GPL 依赖进核心链路；参考 VideoCaptioner 仅限设计思想 |

---

## 5. 输出物规范

### 5.1 目录结构

```
output/
└── 2026-09-28_BV1xxxxxxxxx_视频标题精简/
    ├── notes.md              # 主文档：摘要 + 要点 + 章节导航 + 章节小结
    ├── transcript.refined.md # 精修稿（带段落时间戳）
    ├── transcript.raw.md     # 原始逐字稿（带时间戳，保口语原貌）
    ├── outline.md            # Markdown 大纲
    ├── meta.json             # 元数据 + 处理记录（引擎/模型/耗时/token）
    └── (可选) transcript.srt

work/                          # 中间产物（可断点续跑、可清理）
└── BV1xxxxxxxxx/
    ├── audio.wav / audio.m4a
    ├── asr.json
    ├── refine.chunks.json
    ├── summary.map.json
    └── state.json

logs/
```

### 5.2 `notes.md` 骨架

```markdown
---
title: "<视频标题>"
bvid: "BV1xxxxxxxxx"
url: "https://www.bilibili.com/video/BV1xxxxxxxxx"
up: "<UP主>"
published: 2026-09-01
duration: "42:37"
processed_at: 2026-09-28
asr: "faster-whisper/large-v3"
llm: "deepseek-chat"
tags: ["待定/自动生成"]
---

# <视频标题>

> **一句话结论：** ……

## 核心要点
1. ……（[12:34](跳转链接)）
2. ……

## 章节导航
| 时间 | 章节 | 小结 |
|------|------|------|
| [00:00](url?t=0) | 开场与背景 | …… |

### 章节 1：开场与背景
- 要点……
```

### 5.3 `meta.json` 关键字段

`bvid / cid / title / up / url / pubdate / duration / processed_at / asr.engine / asr.model / asr.cost_time / llm.model / llm.tokens / llm.est_cost / steps[]`

---

## 6. 技术选型（建议，待随架构文档细化）

| 环节 | 选型 | 理由 |
|------|------|------|
| 语言/包管理 | Python 3.11+ / uv | 生态最全，VideoCaptioner 同生态可借鉴 |
| CLI 框架 | Typer + Rich | 参数解析 + 进度条 + 友好报错 |
| 下载 | yt-dlp（首选）/ BBDown（备选） | 活跃维护、只下音频、Cookie 支持成熟 |
| 媒体处理 | ffmpeg（系统安装或随包提供） | 抽音频 / 转码标准方案 |
| ASR | faster-whisper（CTranslate2, CUDA） | 词级时间戳、VAD、速度与质量平衡 |
| ASR 备选 | FunASR SenseVoice-Small | 中文更强更快，本地免费（P1 接入） |
| LLM | OpenAI 兼容 SDK（DeepSeek / opencode-go） | 用户已有渠道，零绑定 |
| 配置 | pydantic-settings + `config.toml` | 类型安全、优先级清晰 |
| 数据模型 | pydantic v2 | 步骤产物结构化落盘 |

---

## 7. 架构草图

```
┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐
│ Resolve  │──▶│ Download │──▶│   ASR    │──▶│ Refine   │
│ BV/URL   │   │ audio only│  │ whisper  │   │ LLM 段级 │
└──────────┘   └──────────┘   └──────────┘   └──────────┘
                                                   │
     ┌─────────────────────────────────────────────┘
     ▼
┌──────────┐   ┌───────────┐   ┌──────────┐   ┌───────────┐
│ Chapters │──▶│ Summarize │──▶│ Outline  │──▶│ Export md │──▶ 清理媒体
│ LLM 切章 │   │ map-reduce│   │ 大纲生成 │   │ +meta     │
└──────────┘   └───────────┘   └──────────┘   └───────────┘

每步：读取 state.json → 已完成则跳过 → 执行 → 落盘 → 更新 state.json
```

---

## 8. 里程碑

| 阶段 | 内容 | 验收 |
|------|------|------|
| M0 | 项目骨架：uv + CLI + 配置 + doctor | `vidar doctor` 全绿 |
| M1 | 下载 + ASR + 原始逐字稿 | 一条 10 分钟视频产出 `transcript.raw.md` |
| M2 | 精修 + 章节 + 摘要 + 大纲 | `notes.md` / `outline.md` 可读可用 |
| M3 | 导出规范 + 续跑 + 清理 + 费用统计 | 中断重跑验证通过 |
| M4（可选） | SenseVoice 引擎 / 术语表 / SRT 导出 | 中文场景对比评测 |

**M1–M3 端到端验收**：选一条 10–20 分钟中文技术类视频（含专业术语），全流程一次跑通；人工检查：摘要无编造、章节时间戳误差 ≤ 3 秒、精修稿忠实原意；同一命令重跑可复用缓存。

---

## 9. 风险与对策

| 风险 | 影响 | 对策 |
|------|------|------|
| B 站接口变动 / 风控 | 下载失败 | 依赖 yt-dlp 更新；错误可读；Cookie 可选支持 |
| Whisper 中文幻觉（复读、幻听句） | 文稿污染 | VAD + 关闭上下文条件 + 幻觉黑名单 + 精修阶段二次过滤 |
| LLM 长文总结丢信息/编造 | 摘要不可信 | map-reduce + 时间锚点 + 「只依据文稿」硬约束 + 自检比对 |
| 精修改写过度 | 偏离原意 | 原始稿永久保留；段级改写；提示词约束不增不减 |
| 显卡/显存不足 | ASR 跑不动 | 模型档位切换（medium → large-v3），CPU 兜底（慢） |
| LLM API 波动/费用超预期 | 任务中断/成本 | 重试 + 分段缓存 + token 统计 + 可选强模型仅用于 reduce |

---

## 10. 待你拍板的开放问题

1. **项目/命令名**：已确认为 **VIDAR**（中文音译「维达」，取"视频达意"之意）；命令 `vidar`，旧命令 `biliking` 保留别名
2. **默认 ASR 模型**：`large-v3` 起步？还是先接 FunASR SenseVoice-Small（中文更快，需多一个依赖）
3. **精修力度**：默认「标准」（去口水词 + 纠错 + 术语规范）是否合适？是否需要「保守/激进」档位

---

## 11. 决策记录（问答结果存档）

- 轮次 1：CLI 优先；单条 BV/URL；本地 ASR（可加免费更优模型）；产出=摘要/章节/双版文稿/大纲/知识库导出；自研参考 VideoCaptioner；Windows + N 卡；自接 OpenAI 兼容 API（DeepSeek / opencode-go）
- 轮次 2：中文为主不区分说话人；一律自跑 ASR；原始+精修两份都要；通用 Markdown 导出；Markdown 大纲；处理后删除音视频

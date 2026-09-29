# VIDAR 架构设计 v0.1

> 配套文档：`docs/requirements.md`（需求规格）。本文回答「怎么实现」。
> 命名约定：CLI 与包名 `vidar`。

---

## 1. 总体结构

```text
CLI (Typer)
  └─ PipelineRunner ── 顺序执行 8 个 Step，步进式落盘 + 断点续跑
       │
       ├─ resolve    解析 BV/URL → VideoMeta
       ├─ download   yt-dlp 只下音频流 → work/<key>/audio.m4a
       ├─ audio      ffmpeg 转 16k 单声道 WAV
       ├─ asr        faster-whisper (CUDA) → asr.json（词级时间戳）
       ├─ refine     LLM 段级精修 → refine.json（原始+精修双文本，时间戳不变）
       ├─ chapters   LLM 章节切分 → chapters.json（标题+起始时间）
       ├─ summarize  map-reduce 摘要 → summary.json（结论/要点/章节小结）
       └─ export     渲染 Markdown → output/... 并清理媒体
```

设计原则：

1. **每一步的产物都是结构化 JSON，落盘在 `work/<key>/`**，可单独检查、可复用、可重跑。
2. **时间戳只在 ASR 阶段产生一次**，后续所有步骤只做「挂载」不做「重排」，保证可追溯性。
3. **LLM 只接收纯文本**（不带时间轴），时间信息由本地按段落映射，降低 token 消耗。
4. 单条视频单进程串行，不做并发（MVP），但每个 Step 是独立函数，便于二期做队列。

---

## 2. 代码结构（含里程碑归属）

```
src/vidar/
├── cli.py                  # Typer 命令：run / doctor / config / gui / version        [M0]
├── config.py               # 配置系统：默认值<TOML<环境变量<CLI                      [M0]
├── doctor.py               # 环境自检（ffmpeg/GPU/依赖/API/目录/磁盘）                [M0]
├── errors.py               # 异常体系（含 CancelledError / MilestoneError）          [M0]
├── logging_setup.py        # 终端 + 日志文件双通道                                   [M0]
├── models.py               # 全部 pydantic 数据模型                                  [M0]
├── source.py               # BV/URL 归一化与分 P 解析                                [M1]
├── downloader.py           # yt-dlp 仅音频下载 + 下载进度                            [M1]
├── asr.py                  # faster-whisper：CUDA DLL/镜像准备、幻觉过滤、段落合并    [M1]
├── render.py               # Markdown 渲染（逐字稿；notes/outline 属 M2）             [M1]
├── pipeline/
│   ├── base.py             # Step 协议 + 执行器（进度事件、取消、续跑）               [M0]
│   ├── context.py          # RunContext：路径/状态/取消/事件                          [M0]
│   ├── state.py            # state.json 状态机                                        [M0]
│   └── steps.py            # 8 个步骤（M1 已实现前 4 个）                             [M1]
├── gui/
│   ├── app.py              # PySide6 主窗口 + 任务线程 + 日志流                       [GUI]
│   └── settings_dialog.py  # 设置面板（写回 config.toml）                             [GUI]
├── llm/
│   ├── client.py           # OpenAI 兼容客户端：重试/JSON 模式/token 记账              [M0]
│   └── prompts.py          # 精修/章节/map/reduce 提示词模板                          [M0]
└── utils/
    ├── ffmpeg.py           # ffmpeg 定位（系统/PATH/imageio-ffmpeg）、抽音频、取消     [M0]
    └── text.py             # 时间格式化/断句/字数统计/slug 化                         [M0]

packaging/                  # PyInstaller 打包（GUI + CLI 双 exe，onedir）
├── vidar.spec           # 双入口共享运行时；收集 CUDA/ffmpeg/Qt/VAD 资源
├── launch_gui.py / launch_cli.py
└── portable/               # 便携包附加文件（config.toml / 使用说明.txt）

scripts/build_portable.ps1  # 一键构建便携版（-Archive 归档到 versions/）
```

## 版本管理（ProjectDock 契约）

- 版本号唯一来源：根目录 `VERSION`（hatchling 动态版本 + 运行时读取）；源码中禁止手写第二份版本号
- 变更同步更新 `CHANGELOG.md`；技术栈文档见 `TECHSTACK.md`
- 构建产物归档：`versions/vX.Y.Z/dist/`（仅本地、只增不删、不覆盖旧产物）

---

## 3. 运行时目录

```text
<当前工作目录>/
├── work/<key>/             # key = BV号（可从URL解析）或 URL 哈希前10位
│   ├── state.json          # 步骤状态机
│   ├── meta.json           # 视频元信息（resolve 产物）
│   ├── audio.m4a           # 下载产物（默认导出后删除）
│   ├── audio.wav           # 16k mono（默认导出后删除）
│   ├── asr.json            # AsrResult
│   ├── refine.json         # RefineResult
│   ├── chapters.json       # list[Chapter]
│   ├── summary.json        # SummaryResult
│   └── run.log
├── output/<日期>_<BV>_<标题slug>/
│   ├── notes.md / transcript.raw.md / transcript.refined.md / outline.md / meta.json
└── logs/vidar.log
```

`state.json` 步骤状态：`pending / running / done / failed`。加载时把遗留的 `running` 重置为 `pending`（视为崩溃）。

**续跑语义**：`done` 的步骤直接跳过；产物文件缺失的 `done` 步骤降级重跑。

---

## 4. 核心数据模型（pydantic v2）

| 模型 | 字段要点 | 产生步骤 |
|------|----------|----------|
| `VideoMeta` | bvid/cid/page/title/up_name/url/pubdate/duration_sec | resolve |
| `AsrWord` / `AsrSegment` / `AsrResult` | 词级 start/end/text + 段级聚合 | asr |
| `RefinedSegment` / `RefineResult` | idx/start/end/raw_text/refined_text + tokens | refine |
| `Chapter` | index/title/start/summary | chapters → summarize 回填 |
| `SummaryResult` | one_liner/key_points[{text,start}]/chapters[] | summarize |
| `RunState` | steps: dict[str, StepRecord(status/error/artifacts)] | runner |

时间一律用「秒(float)」存储，格式化只在导出层做。

---

## 5. ASR 设计（faster-whisper）

```python
WhisperModel(model_size, device="cuda", compute_type="float16")   # 8G 显存可跑 large-v3
segments, info = model.transcribe(
    "audio.wav",
    language="zh",
    vad_filter=True,                      # Silero VAD，防静音段幻觉
    condition_on_previous_text=False,     # 防复读
    word_timestamps=True,
    beam_size=5,
    initial_prompt="以下是普通话视频转写，请输出简体中文，保留标点。",
)
```

- 模型档位：`large-v3`（默认，8G 显存 float16 可行）/ `large-v3-turbo`（更省显存更快）/ `medium`。
- **幻觉过滤**：命中黑名单短语（「请点赞投币」「字幕由……提供」「谢谢观看」等）且时长 < 2s 的段直接丢弃；连续 N 段文本高度重复时告警。
- **引擎抽象**：`AsrEngine` 协议（`name / transcribe(wav) -> AsrResult`），二期可插入 FunASR SenseVoice。
- Blackwell（RTX 50 系）注意：ctranslate2 需要 CUDA 12.8+ 的运行时，doctor 会检查 `ctranslate2.get_cuda_device_count()`，失败自动建议 `device="cpu"` 降级。

**为什么精修按「段」而不按「词」**：词级重排（VideoCaptioner 的模糊匹配方案）时间戳会有漂移风险；段级改写结束后，段落 start/end 原样保留，误差恒为 0，代价是段内断句粒度不变。

段落粒度规则：ASR 相邻小段合并，直到累计 ≥ 40 字或遇到 ≥ 1.2s 停顿为止（约 3–15 秒/段，适合朗读与阅读）。

---

## 6. LLM 调用设计

### 6.1 客户端

- OpenAI 兼容（DeepSeek / opencode-go / 任意 base_url），`openai` SDK。
- 重试：指数退避，`max_retries` 次；超时可配。
- 输出：优先 `response_format={"type": "json_object"}`；失败则从文本中提取首个 JSON 对象兜底。
- 记账：每次调用累计 `prompt_tokens / completion_tokens`，写入产物 JSON 与 `meta.json`。

### 6.2 分块策略

| 步骤 | 分块 | 说明 |
|------|------|------|
| refine | 按段落打包，~3500 字/块 | 块内并发无意义（顺序调用），块间独立 |
| summarize.map | 按段落打包，~3500 字/块 | 每块输出 1–3 条要点 + 段号锚点 |
| summarize.reduce | 单次 | 输入为全部 map 要点（已压缩） |
| chapters | 单次（输入全文精修稿） | 长视频若超上下文则按 map 结果二次归并 |

### 6.3 提示词要点（全文见 `llm/prompts.py`）

- **精修**：只去口水词 / 纠错字 / 规范术语标点；不增不减；保留数字与英文术语；输出 `{"refined": "..."}`；可注入用户术语表。
- **章节**：输入带段号的精修稿，输出 `{"chapters":[{"title","start_index"}]}`；标题 ≤ 14 字，长视频 3–10 分钟一章。
- **map**：输出 `{"points":[{"text","anchor"}]}`，要点带锚点段号。
- **reduce**：只依据给定要点，输出「一句话结论 + 5–10 条核心要点 + 每章小结」；不确定就省略（防编造硬约束）。
- 所有步骤提示词统一附加：`不要输出解释或 Markdown 代码块，只输出 JSON`。

### 6.4 时间锚点恢复

map/reduce 输出的锚点段号 → 本地查段落 start → 导出为 `[mm:ss]` 与 `BV?t=秒` 链接。LLM 全程不接触时间数字，杜绝时间戳幻觉。

---

## 7. 导出规范

- 目录名：`{yyyymmdd}_{bvid}_{title_slug}`；slug 保留中文，替换 Windows 非法字符，截断 40 字。
- `notes.md`：frontmatter（title/bvid/url/up/published/duration/processed_at/asr/llm/model 信息）→ 一句话结论 → 核心要点（带跳转）→ 章节表格 → 每章小结。
- `outline.md`：**确定性生成**（不额外调 LLM）：结论 → 每章标题（时间）→ 其下要点。
- `transcript.raw.md` / `transcript.refined.md`：每段 `[mm:ss] 文本`。
- 清理：默认删除 `work/<key>/*.m4a|*.wav`（保留全部 JSON）；`--keep-media` / `--keep-audio` 覆盖。

---

## 8. 错误处理与日志

| 场景 | 行为 |
|------|------|
| 依赖缺失（ffmpeg/yt-dlp/faster-whisper） | `DependencyError`，打印安装指引，退出码 2 |
| 网络/LLM 失败 | 自动重试；仍失败则步骤标记 failed，退出码 1，可重跑续传 |
| 步骤未实现（里程碑未到） | `MilestoneError`，明确提示所属里程碑 |
| 日志 | 终端 Rich（正常信息 info / 详细 -v）+ 文件 `logs/vidar.log`（含每步耗时） |

---

## 9. 测试策略

- 单元测试：文本工具（时间格式/slug/断句/分块）、状态机（保存/加载/崩溃恢复/续跑）、配置（优先级合并/掩码）。
- 集成测试（M1+）：可用 `--dry-run` 验证计划；下载/ASR 用一条固定短视频做冒烟测试，网络失败可跳过。
- 人工验收：见需求文档 §8（摘要无编造、章节误差 ≤3s、精修忠实原意）。

---

## 10. 里程碑任务拆分

| 阶段 | 内容 | 状态 |
|------|------|------|
| M0 | CLI/配置/doctor/状态机/文本工具/测试骨架 | ✅ |
| M1 | resolve + download + audio + asr + transcript.raw（含 CUDA DLL 自动配置） | ✅ |
| GUI | PySide6 单窗口：进度/日志/历史/设置/取消/断点重跑 | ✅ |
| M2 | refine + chapters + summarize + export（端到端产出 notes.md，假 LLM 集成测试通过） | ✅ |
| M3 | 导出增强（SRT 开关、token 统计已入 meta.json）+ 清理策略打磨 + 文档完善 | 下一步 |
| M4 | SenseVoice 引擎 / 术语表 / SRT / 批量队列（可选） | |

## 11. 关键决策记录（ADR 摘要）

1. **只下音频流**：媒体处理完即删，音频体积 ≈ 视频的 1/10。
2. **段级精修**：以 0 时间戳漂移换取精修粒度，可追溯性优先。
3. **LLM 不接触时间轴**：时间锚点全部本地映射。
4. **产物即接口**：每步 JSON 落盘，既是缓存也是调试接口，天然支持续跑与并行化改造。
5. **自研不抄码**：仅借鉴 VideoCaptioner 的设计思想（VAD+词级时间戳、文稿匹配思路），规避 GPL-3.0。

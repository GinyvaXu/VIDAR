# AGENTS.md — 项目契约（由 ProjectDock 生成）

VIDAR：B 站视频 → 可读可检索的 Markdown 知识文档（解析 / ASR / 精修 / 摘要 / 导出；uv + Python）。本项目由 ProjectDock 管理，以下是必须遵守的规范。

## 项目结构
- 类型：软件（Python 软件项目）
- 目录结构约定：src、tests
- 骨架文件约定：README.md、VERSION、CHANGELOG.md、TECHSTACK.md、requirements.txt、.gitignore、AGENTS.md

## 版本管理（强制）
- 版本号唯一来源：根目录 `VERSION` 文件（纯数字 semver，如 0.1.0）；禁止在源码/spec/安装器里手写第二份版本号
- 每次变更同步更新 `CHANGELOG.md` 更新日志（## [版本] - 日期 + ### Added/Fixed/Changed 分组）
- 构建产物归档到 `versions/vX.Y.Z/dist/`（仅本地、不上传、只增不删、不覆盖旧产物）
- 语义化版本：Bug 修复=PATCH、新功能/UI=MINOR、不兼容大改=MAJOR

## Git 规范
- 单 main 分支直接开发与发布，不建 develop/feature 分支
- 提交前缀：feat: / fix: / release: / build: / chore: / docs: / refactor: / test:
- 提交前用 git status + git diff --stat 复核改动范围，不提交无关文件
- **不主动 push**；推送 GitHub 由用户明确要求后进行，推送前复核内容
- versions/ 与 dist/ 只增不删、仅本地保留、不上传

## 任务流程（强制）
1. 大功能先调研 → 与用户 grill 确认方案 → update_plan 拆解 → 逐步实现 → 交付报告
2. 重要修改先与用户商讨，不一键直达
3. 任务执行前先做安全备份（快照进 versions/backups/）
4. 每轮迭代结束交付报告：改动清单 / 构建产物路径 / 测试建议

## 归档优先（先听懂再动手）
- 用户说「整理版本归档 / 归档构建产物 / 把产物归到版本里」= 把根目录 `dist/`、`installer/`、`build/` 的构建产物移动归档到 `versions/vX.Y.Z/dist/`（文件名带版本号的按各自版本归档，否则用 VERSION 的当前版本）。
- 用户只提「归档 / 整理版本」时：**不要擅自修改 CHANGELOG.md、VERSION 或发版本号**；先归档，再报告并询问是否需要补更新日志。
- 只有用户明确说「发布 / 发版 / release」才走 VERSION -> CHANGELOG -> git tag -> Release 完整流程。
- 归档用 `python -m projectdock.cli archive <项目名> [--version X.Y.Z] [--confirm]`；归档是移动不是删除，绝不覆盖旧产物。

## 技术栈文档（TECHSTACK.md）
- 软件项目根目录应维护 `TECHSTACK.md`（ProjectDock「技术栈」Tab 展示、合规必需项）。
- 格式（详细但简明清晰）：
  - `## 概览` 表格：语言/运行时、主要框架、数据存储、前端、构建与打包、测试等；
  - `## 核心功能实现`：每个关键功能一个 `### 小节`，含 `- **实现逻辑**：...` 与 `- **技术手段**：...` 两条要点。
- 何时写/更新：用户要求「写/补/更新技术栈」、每次涉及重要技术选型或新增核心功能后。
- 撰写方法：先通读源码、README、依赖清单、CHANGELOG，再归纳核心功能；不夸大、不写未实现的内容。
- 工具：`python -m projectdock.cli techstack <项目名>` 可生成模板或查看当前内容。

## 确认策略（强制）
- 以下操作必须先获得**用户明确同意**才能执行：推送 GitHub（push）/ 删除文件（delete）/ 创建 GitHub 仓库（github_create）/ 发布 Release（release）/ 归档移动构建产物（archive）
- 执行上述操作时：`projectdock-cli` 对应命令要求加 `--confirm` 参数；外部 agent 不得擅自 push / 删除 / 建仓
- 用户可在 ProjectDock 设置中逐项关闭确认（关闭后该操作可自动执行），但删除文件类操作始终建议先备份

## AI 操作日志（强制）
- 每个任务完成后必须主动写 AI 操作日志到 `logs/ai/`（JSON），位置与格式见下；**不写日志视为未完成任务**
- 要素：ts / agent / action / result / summary / details / git / backup
- 快捷方式：`python -m projectdock.cli log <项目名> --agent <你的名字> --action "..." --result done --summary "..."`

## 与 ProjectDock 对接（projectdock-cli）
- 查看契约与当前状态：`python -m projectdock.cli context <项目名>`；查看 AI 日志：`python -m projectdock.cli logs <项目名>`
- 新建项目：`python -m projectdock.cli init <名称> --type <类型> [--no-git]`
- 构建并归档：`python -m projectdock.cli build <项目名> [--script 脚本] [--archive]`
- 发布版本：`python -m projectdock.cli release <项目名> --version X.Y.Z [--changelog "..."] [--build 脚本] [--push] [--confirm]`
- 归档根目录产物：`python -m projectdock.cli archive <项目名> [--version X.Y.Z] [--confirm]`
- 若上述命令不可用：按本契约手动维护文件，并把日志 JSON 写到 logs/ai/ 即可

## 禁止事项
- 不删除 versions/、dist/ 内容；不覆盖旧构建产物（同版本重建先带时间戳归档旧 exe）
- 不把私有配置（config.json / *.env / API Key）提交入库
- 不离开当前项目目录做无关操作

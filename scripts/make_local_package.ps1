# 从发布包生成"本机调试包"：模型路径 + API Key + 导出开关全部预配好，开箱即用
# 用法:
#   powershell -ExecutionPolicy Bypass -File scripts/make_local_package.ps1 `
#       -ReleaseDir "versions/v0.4.0/dist/VIDAR-v0.4.0-win64-portable" `
#       -OutDir "versions/v0.4.0/local/VIDAR-v0.4.0-win64-local" -UpdateLink

param(
    [Parameter(Mandatory = $true)][string]$ReleaseDir,
    [Parameter(Mandatory = $true)][string]$OutDir,
    [string]$ModelPath = "",
    [string]$LinkPath = "local",
    [switch]$UpdateLink
)

$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")
$sharedRoot = (Get-Location).Path.Replace("\", "/")

if (-not (Test-Path $ReleaseDir)) { throw "发布包不存在：$ReleaseDir" }
if (Test-Path $OutDir) { throw "本机包已存在，拒绝覆盖：$OutDir" }

Write-Output "复制应用目录 → $OutDir"
New-Item -ItemType Directory -Force (Split-Path $OutDir) | Out-Null
Copy-Item $ReleaseDir $OutDir -Recurse

# 模型路径（默认：项目根 models/faster-whisper-large-v3）
if (-not $ModelPath) { $ModelPath = Join-Path (Get-Location) "models\faster-whisper-large-v3" }
$modelForToml = ([string]$ModelPath).Replace("\", "/")

# API Key：优先用户级环境变量（不回显密钥）
$key = [Environment]::GetEnvironmentVariable("OPENCODE_GO_API_KEY", "User")
if (-not $key) { $key = $env:OPENCODE_GO_API_KEY }
if (-not $key) {
    Write-Warning "未找到 OPENCODE_GO_API_KEY：本机包内 api_key 留空（可在 GUI 设置中填写）"
}

$configNewline = "`r`n"
$config = @"
# VIDAR 本机调试包（自动生成：模型路径与 API 已配置好，开箱即用）
# !! 本文件包含你的 API Key —— 请勿分享/上传本文件夹 !!
# 工作/输出目录与主项目共享：历史任务跨版本保留，升级不丢

[paths]
output_dir = "$sharedRoot/output"
work_dir = "$sharedRoot/work"
logs_dir = "$sharedRoot/logs"

[asr]
model = "$modelForToml"
device = "auto"

[llm]
api_base = "https://opencode.ai/zen/go/v1"
api_key = "$key"
model = "deepseek-v4.1-flash"
timeout_sec = 180
max_retries = 3
chunk_chars = 3500

[export]
screenshots = true
pdf = true
pdf_style = "newspaper"
pdf_mode = "full"
pdf_scale = "standard"
point_figures = "all"
docx = true
"@
$config = $config -replace "`r?`n", $configNewline
[IO.File]::WriteAllText((Join-Path $OutDir "config.toml"), $config, (New-Object System.Text.UTF8Encoding($false)))

$readme = @"
VIDAR 本机调试包
================
本包已预配置：模型路径、LLM API、GPU 运行时安装记录（如已安装）、PDF/Word 导出。
工作/输出目录与主项目共享（$sharedRoot）：历史任务跨版本保留，升级不丢。

- 双击 VIDAR.exe 直接使用
- 注意：config.toml 含你的 API Key，请勿分享/上传本文件夹
- 稳定入口：项目根目录的 local\VIDAR.exe（每次新版本构建后自动指向最新本机包）
"@
[IO.File]::WriteAllText(
    (Join-Path $OutDir "使用说明（本机版）.txt"),
    ($readme -replace "`r?`n", $configNewline),
    (New-Object System.Text.UTF8Encoding($false))
)

if ($UpdateLink) {
    $link = Join-Path (Get-Location) $LinkPath
    if (Test-Path $link) { cmd /c rmdir "$link" | Out-Null }
    New-Item -ItemType Junction -Path $link -Target (Resolve-Path $OutDir).Path | Out-Null
    Write-Output "已更新稳定入口：$link → $OutDir"
}

Write-Output "本机调试包完成：$OutDir"

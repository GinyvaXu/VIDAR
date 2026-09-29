# 构建 VIDAR 便携版（PyInstaller onedir）
# 用法:
#   powershell -ExecutionPolicy Bypass -File scripts/build_portable.ps1
#   powershell -ExecutionPolicy Bypass -File scripts/build_portable.ps1 -Archive   # 归档到 versions/vX.Y.Z/dist/

param(
    [switch]$SkipSync,
    [switch]$Archive
)

$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

$uv = (Get-Command uv -ErrorAction SilentlyContinue).Source
if (-not $uv) { $uv = "$env:APPDATA\Python\Python314\Scripts\uv.exe" }
if (-not (Test-Path $uv)) { throw "未找到 uv，请先安装：https://docs.astral.sh/uv/" }

if (-not $SkipSync) {
    & $uv sync --extra asr --extra gui
}

& $uv run pyinstaller packaging/vidar.spec --noconfirm --distpath dist --workpath build/pyinstaller

$portable = "dist/VIDAR"
Copy-Item "packaging/portable/config.toml" "$portable/config.toml" -Force
Copy-Item "packaging/portable/使用说明.txt" "$portable/使用说明.txt" -Force
New-Item -ItemType Directory -Force "$portable/models" | Out-Null

$version = (Get-Content VERSION -Raw).Trim()
if ($Archive) {
    $dest = "versions/v$version/dist/VIDAR-v$version-win64-portable"
    if (Test-Path $dest) { throw "归档目标已存在，拒绝覆盖旧产物：$dest" }
    New-Item -ItemType Directory -Force (Split-Path $dest) | Out-Null
    Move-Item $portable $dest
    Write-Output "已归档：$dest"
} else {
    Write-Output "构建完成：$portable"
}

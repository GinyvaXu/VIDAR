# 上传 Release 资产（用于网络恢复 / VPN 开启后补传）
# 背景：部分网络环境下，向 uploads.github.com 上传较大的文件会 0 字节卡死；
#       本脚本带进度与重试，网络正常后一条命令即可补传。
#
# 用法:
#   powershell -ExecutionPolicy Bypass -File scripts/upload_release_asset.ps1 `
#       -File "versions/v0.3.1/dist/VIDAR-v0.3.1-win64-portable.zip"

param(
    [Parameter(Mandatory = $true)][string]$File,
    [string]$Tag = "",
    [string]$Repo = "GinyvaXu/VIDAR",
    [int]$Attempts = 4
)

$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

if (-not (Test-Path $File)) { throw "文件不存在：$File" }
$item = Get-Item $File
Write-Output ("待上传：{0}（{1:N1} MB）" -f $item.Name, ($item.Length / 1MB))

# 1) 定位 Release（默认最新草稿或指定 tag）
$releases = gh api "repos/$Repo/releases?per_page=30" | ConvertFrom-Json
$release = if ($Tag) {
    $releases | Where-Object { $_.tag_name -eq $Tag } | Select-Object -First 1
} else {
    $releases | Where-Object { $_.draft -eq $true } | Select-Object -First 1
}
if (-not $release) { throw "未找到目标 Release（可用 -Tag 指定，如 v0.3.1）" }
Write-Output ("目标 Release：{0}（id={1}，草稿={2}）" -f $release.name, $release.id, $release.draft)

# 2) 带重试上传
$token = (gh auth token).Trim()
$ok = $false
for ($i = 1; $i -le $Attempts -and -not $ok; $i++) {
    Write-Output ("尝试 {0}/{1} 上传…" -f $i, $Attempts)
    curl.exe --max-time 3600 --progress-bar `
        -X POST "https://uploads.github.com/repos/$Repo/releases/$($release.id)/assets?name=$([uri]::EscapeDataString($item.Name))" `
        -H "Authorization: Bearer $token" `
        -H "Content-Type: application/octet-stream" `
        -H "Expect:" `
        --data-binary "@$($item.FullName)"
    $ok = ($LASTEXITCODE -eq 0)
    if (-not $ok) { Write-Output "本次失败，15 秒后重试…"; Start-Sleep -Seconds 15 }
}

if (-not $ok) {
    Write-Output "✗ 上传多次失败：当前网络可能仍无法访问 GitHub 上传端点（建议开启代理后重试）"
    exit 1
}

Write-Output "✓ 上传成功"
$latest = gh release view --repo $Repo --json isDraft,assets | ConvertFrom-Json
$latest.assets | Select-Object name, @{N = 'MB'; E = { [math]::Round($_.size / 1MB, 2) } }, state | Format-Table -AutoSize
if ($latest.isDraft) {
    Write-Output "提示：Release 仍是草稿，确认资产齐全后执行：gh release edit --repo $Repo <tag> --draft=false"
}

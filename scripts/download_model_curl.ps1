# 用 curl 分片并行下载 faster-whisper 模型（绕开 HF 客户端的 xet 传输问题）
# 用法:
#   powershell -File scripts/download_model_curl.ps1
#   powershell -File scripts/download_model_curl.ps1 -ModelId "Systran/faster-whisper-large-v3" -Connections 6

param(
    [string]$ModelId = "Systran/faster-whisper-large-v3",
    [string]$DestDir = "models/faster-whisper-large-v3",
    [int]$Connections = 6,
    [string]$Endpoint = "https://hf-mirror.com"
)

$ErrorActionPreference = "Stop"
New-Item -ItemType Directory -Force -Path $DestDir | Out-Null

# ---- 小文件 ----
$smallFiles = @("config.json", "tokenizer.json", "vocabulary.json", "preprocessor_config.json")
foreach ($name in $smallFiles) {
    $out = Join-Path $DestDir $name
    if ((Test-Path $out) -and (Get-Item $out).Length -gt 0) {
        Write-Output "跳过 $name（已存在）"
        continue
    }
    curl.exe -L --fail -s -o $out "$Endpoint/$ModelId/resolve/main/$name"
    if ($LASTEXITCODE -ne 0) { throw "下载失败: $name" }
    Write-Output "已下载 $name"
}

# ---- model.bin 分片并行 ----
$url = "$Endpoint/$ModelId/resolve/main/model.bin"
$out = Join-Path $DestDir "model.bin"
if ((Test-Path $out) -and (Get-Item $out).Length -gt 100MB) {
    Write-Output ("model.bin 已存在（{0:N2} GB），跳过" -f ((Get-Item $out).Length / 1GB))
    exit 0
}

$headers = curl.exe -sIL "$url"
$match = [regex]::Matches(($headers -join "`n"), "(?im)^content-length:\s*(\d+)") | Select-Object -Last 1
if (-not $match) { throw "无法获取 model.bin 大小" }
$size = [long]$match.Groups[1].Value
Write-Output ("model.bin 总大小: {0:N2} GB，启用 {1} 个分片并行下载" -f ($size / 1GB), $Connections)

$chunk = [math]::Ceiling($size / $Connections)
$jobs = @()
for ($i = 0; $i -lt $Connections; $i++) {
    $start = [long]($i * $chunk)
    if ($start -ge $size) { break }
    $end = [math]::Min([long]($start + $chunk - 1), [long]($size - 1))
    $part = Join-Path $DestDir ("model.bin.part{0:d2}" -f $i)
    $jobs += Start-Job -ScriptBlock {
        param($u, $s, $e, $o)
        curl.exe -L --fail -s --max-time 14400 -r "$s-$e" -o $o $u
        if ($LASTEXITCODE -ne 0) { throw "分片下载失败: $o" }
    } -ArgumentList $url, $start, $end, $part
}

$lastReport = Get-Date
while ($jobs | Where-Object { $_.State -eq "Running" }) {
    Start-Sleep -Seconds 20
    $done = (Get-ChildItem (Join-Path $DestDir "model.bin.part*") -ErrorAction SilentlyContinue |
        Measure-Object -Property Length -Sum).Sum
    Write-Output ("进度: {0:N2} / {1:N2} GB（{2:N0}%）" -f ($done / 1GB), ($size / 1GB), (100 * $done / $size))
}
$failed = $jobs | Where-Object { $_.State -eq "Failed" }
if ($failed) {
    $failed | ForEach-Object { Write-Output ("分片错误: " + ($_ | Receive-Job)) }
    exit 1
}
$jobs | Remove-Job -Force

# ---- 合并分片 ----
$final = [System.IO.File]::Create($out)
try {
    for ($i = 0; $i -lt $Connections; $i++) {
        $part = Join-Path $DestDir ("model.bin.part{0:d2}" -f $i)
        if (-not (Test-Path $part)) { continue }
        $stream = [System.IO.File]::OpenRead($part)
        try { $stream.CopyTo($final) } finally { $stream.Close() }
        Remove-Item $part -Force
    }
} finally { $final.Close() }

Write-Output ("model.bin 完成: {0:N2} GB → {1}" -f ((Get-Item $out).Length / 1GB), $out)

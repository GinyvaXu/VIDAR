# 自动截取 VIDAR GUI 窗口截图（用于 README）
# 用法: powershell -ExecutionPolicy Bypass -File scripts/capture_screenshot.ps1 [-Wait 30]

param([int]$Wait = 30)

$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Drawing

Add-Type @"
using System;
using System.Runtime.InteropServices;
public class NativeWin {
    [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr hWnd, out RECT rect);
    public struct RECT { public int Left; public int Top; public int Right; public int Bottom; }
}
"@
[NativeWin]::SetProcessDPIAware() | Out-Null

$root = Split-Path $PSScriptRoot -Parent
Set-Location $root

# 清理可能存在的旧实例（含旧名窗口），避免截错
Get-Process | Where-Object { $_.MainWindowTitle -like "*VIDAR*" -or $_.MainWindowTitle -like "*BiliVideoKing*" } |
    ForEach-Object { Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue }
Start-Sleep -Seconds 1

$exe = Join-Path $root ".venv\Scripts\python.exe"
Start-Process -FilePath $exe -ArgumentList "-m", "vidar.gui.app" -WorkingDirectory $root | Out-Null
Write-Output "GUI 已启动，最多等待 $Wait 秒寻找窗口…"

$window = $null
$deadline = (Get-Date).AddSeconds($Wait)
while ((Get-Date) -lt $deadline) {
    $window = Get-Process | Where-Object { $_.MainWindowTitle -like "*VIDAR*" } | Select-Object -First 1
    if ($window) { break }
    Start-Sleep -Milliseconds 500
}
if (-not $window) { throw "未找到 VIDAR 窗口（可加大 -Wait 后重试）" }

Write-Output ("找到窗口：{0}（PID {1}）" -f $window.MainWindowTitle, $window.Id)
[NativeWin]::SetForegroundWindow($window.MainWindowHandle) | Out-Null
Start-Sleep -Seconds 2

$rect = New-Object NativeWin+RECT
[NativeWin]::GetWindowRect($window.MainWindowHandle, [ref]$rect) | Out-Null
$width = $rect.Right - $rect.Left
$height = $rect.Bottom - $rect.Top

New-Item -ItemType Directory -Force "docs\images" | Out-Null
$bitmap = New-Object System.Drawing.Bitmap($width, $height)
$graphics = [System.Drawing.Graphics]::FromImage($bitmap)
$graphics.CopyFromScreen($rect.Left, $rect.Top, 0, 0, $bitmap.Size)
$target = Join-Path $root "docs\images\gui.png"
$bitmap.Save($target, [System.Drawing.Imaging.ImageFormat]::Png)
$graphics.Dispose()
$bitmap.Dispose()
Stop-Process -Id $window.Id -Force -ErrorAction SilentlyContinue
Write-Output "截图完成：$target（${width}x${height}）"

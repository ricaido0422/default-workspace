# 美化桌面快捷方式：重命名 + 指向 Edge 应用窗口（无黑框）+ 新图标 + 提示文字
$ErrorActionPreference = 'Stop'

$desktop   = 'D:\Users\Administrator\Desktop'
$oldLnk    = Join-Path $desktop 'Edge版.bat.lnk'
$newLnk    = Join-Path $desktop 'DeepSeek 助手.lnk'
$backupDir = 'D:\Users\Administrator\Documents\deepseek-harness\default-workspace\deepseek-desktop\backup'
$icon      = 'D:\Users\Administrator\Documents\deepseek-harness\default-workspace\deepseek-desktop\assets\icon.ico'
$edge      = 'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe'
$profile   = Join-Path $env:LOCALAPPDATA 'DeepSeekAssistant'

if (-not (Test-Path -LiteralPath $edge))    { throw "Edge not found: $edge" }
if (-not (Test-Path -LiteralPath $icon))    { throw "Icon not found: $icon" }
New-Item -ItemType Directory -Force -Path $backupDir | Out-Null

# 备份原快捷方式
if (Test-Path -LiteralPath $oldLnk) {
    Copy-Item -LiteralPath $oldLnk -Destination (Join-Path $backupDir 'Edge版.bat.lnk.orig') -Force
}
if (Test-Path -LiteralPath $newLnk) {
    Copy-Item -LiteralPath $newLnk -Destination (Join-Path $backupDir 'DeepSeek 助手.lnk.prev') -Force
}

# 重命名（保留原快捷方式本体）
if (Test-Path -LiteralPath $oldLnk) {
    Move-Item -LiteralPath $oldLnk -Destination $newLnk -Force
}
if (-not (Test-Path -LiteralPath $newLnk)) { throw "Shortcut missing: $newLnk" }

$sh = New-Object -ComObject WScript.Shell
$s = $sh.CreateShortcut($newLnk)
$s.TargetPath       = $edge
$s.Arguments        = '--app="https://chat.deepseek.com/" --user-data-dir="' + $profile + '"'
$s.WorkingDirectory = Split-Path -Parent $edge
$s.IconLocation     = $icon + ',0'
$s.Description      = 'DeepSeek 助手 — 独立窗口，专注对话（Edge 内核）'
$s.WindowStyle      = 1
$s.Hotkey           = ''
$s.Save()

# 验证
$v = $sh.CreateShortcut($newLnk)
[pscustomobject]@{
    Path             = $newLnk
    TargetPath       = $v.TargetPath
    Arguments        = $v.Arguments
    WorkingDirectory = $v.WorkingDirectory
    IconLocation     = $v.IconLocation
    Description      = $v.Description
    WindowStyle      = $v.WindowStyle
    TargetExists     = (Test-Path -LiteralPath $v.TargetPath)
} | Format-List

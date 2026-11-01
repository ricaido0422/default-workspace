<#
  gh-setup.ps1 — GitHub CLI (gh) 安装 + 授权 向导 (Windows / PowerShell)

  官方来源: https://cli.github.com/   实际下载走其官方 GitHub Releases (cli/cli)
  用法:
      powershell -ExecutionPolicy Bypass -File .\gh-setup.ps1

  阶段:
      1/4  环境检查
      2/4  安装 / 更新 gh CLI (已装则跳过)
      3/4  浏览器授权 (gh auth login 设备码流程)
      4/4  验证

  安全说明: 本脚本不接收、不记录、不落盘你的任何凭据。
            授权全程由 gh 官方设备码流程在浏览器内完成。
#>

$ErrorActionPreference = 'Stop'
$ProgressPreference    = 'SilentlyContinue'

$TotalStages = 4
$GhDir       = Join-Path $env:LOCALAPPDATA 'Programs\gh'
$GhBin       = Join-Path $GhDir 'bin'

function Stage([int]$n, [string]$title) {
    Clear-Host
    Write-Host ('=' * 66) -ForegroundColor DarkCyan
    Write-Host ('  阶段 {0}/{1}   {2}' -f $n, $TotalStages, $title) -ForegroundColor Cyan
    Write-Host ('=' * 66) -ForegroundColor DarkCyan
    Write-Host ''
}
function Say([string]$t)      { Write-Host $t }
function Ok([string]$t)       { Write-Host "  [OK] $t"   -ForegroundColor Green }
function Warn([string]$t)     { Write-Host "  [!]  $t"   -ForegroundColor Yellow }
function Wait-Key {
    Write-Host ''
    Write-Host '  按 Enter 继续...' -ForegroundColor DarkGray
    [void](Read-Host)
}
function Get-GhPath {
    $c = Get-Command gh -ErrorAction SilentlyContinue
    if ($c) { return $c.Source }
    $p = Join-Path $GhBin 'gh.exe'
    if (Test-Path $p) { return $p }
    return $null
}
function Add-UserPath([string]$dir) {
    $cur = [Environment]::GetEnvironmentVariable('Path','User')
    if ($cur -notlike "*$dir*") {
        $new = if ([string]::IsNullOrEmpty($cur)) { $dir } else { "$cur;$dir" }
        [Environment]::SetEnvironmentVariable('Path', $new, 'User')
        Ok "已加入用户 PATH: $dir (新开的终端生效)"
    } else {
        Ok "用户 PATH 已包含: $dir"
    }
}

# ─────────────────────────── 1/4  环境检查 ───────────────────────────
Stage 1 '环境检查'
Say ("系统: " + (Get-CimInstance Win32_OperatingSystem).Caption)
Say ("PowerShell: " + $PSVersionTable.PSVersion.ToString())
$gh = Get-GhPath
if ($gh) {
    Ok "已检测到 GitHub CLI"
    Say ('  ' + (& $gh --version | Select-Object -First 1))
} else {
    Warn '未检测到 GitHub CLI —— 下一阶段将自动安装。'
}
Wait-Key

# ────────────────────── 2/4  安装 / 更新 gh CLI ──────────────────────
Stage 2 '安装 / 更新 GitHub CLI'
$gh = Get-GhPath
if ($gh) {
    Ok "已经安装,跳过安装: $gh"
} else {
    $installed = $false

    # 方式 A: winget (微软官方源)
    if (-not $installed -and (Get-Command winget -ErrorAction SilentlyContinue)) {
        Say '尝试 winget 安装 (GitHub.cli) ...'
        try {
            winget install --id GitHub.cli --source winget `
                --accept-package-agreements --accept-source-agreements --silent
            if (Get-GhPath) { $installed = $true }
        } catch {
            Warn 'winget 不可用,改用官方 zip。'
        }
    }

    # 方式 B: 官方 GitHub Releases 的 Windows zip (无需管理员)
    if (-not $installed) {
        Say '查询官方最新版本 ...'
        $rel = Invoke-RestMethod -Uri 'https://api.github.com/repos/cli/cli/releases/latest' `
                                 -Headers @{ 'User-Agent' = 'gh-setup-script' }
        $asset = $rel.assets |
            Where-Object { $_.name -like '*windows_amd64.zip' } |
            Select-Object -First 1
        if (-not $asset) { throw '官方 release 中未找到 windows_amd64.zip 资产。' }

        $zip = Join-Path $env:TEMP $asset.name
        Say ("下载 " + $asset.name + " ...")
        Invoke-WebRequest -Uri $asset.browser_download_url -OutFile $zip -UseBasicParsing

        $ext = Join-Path $env:TEMP ('gh-extract-' + [guid]::NewGuid().ToString('N'))
        Expand-Archive -Path $zip -DestinationPath $ext -Force

        $exe = Get-ChildItem $ext -Recurse -Filter gh.exe | Select-Object -First 1
        if (-not $exe) { throw '解压后未找到 gh.exe。' }
        $pkgroot = Split-Path $exe.DirectoryName -Parent

        if (Test-Path $GhDir) { Remove-Item $GhDir -Recurse -Force }
        Copy-Item $pkgroot $GhDir -Recurse -Force
        Ok "已安装到: $GhDir"
        Add-UserPath $GhBin

        Remove-Item $ext -Recurse -Force -ErrorAction SilentlyContinue
        Remove-Item $zip -Force             -ErrorAction SilentlyContinue

        $env:Path = "$env:Path;$GhBin"
        $installed = $true
    }

    if ($installed) {
        $gh = Get-GhPath
        Ok '安装完成'
        if ($gh) { Say ('  ' + (& $gh --version | Select-Object -First 1)) }
    } else {
        throw '安装失败,请手动安装: https://cli.github.com/'
    }
}
Wait-Key

# ──────────────────────── 3/4  浏览器授权 ────────────────────────
Stage 3 '浏览器授权 (gh auth login)'
$gh = Get-GhPath
if (-not $gh) { throw '未找到 gh,无法授权。' }

$status = & $gh auth status 2>&1
if ($LASTEXITCODE -eq 0) {
    Ok '当前已处于已登录状态,跳过授权。'
    $status | ForEach-Object { Say ('  ' + $_) }
} else {
    Say '接下来会走 GitHub 官方「设备码」流程:'
    Say ''
    Say '  1) 终端会显示一个一次性设备码 (XXXX-XXXX)'
    Say '  2) 浏览器会自动打开 https://github.com/login/device'
    Say '     若未自动打开,手动访问该地址'
    Say '  3) 粘贴设备码 -> Continue -> Authorize GitHub CLI'
    Say ''
    Say '请在浏览器里完成上述步骤,本脚本会在此等待......'
    Write-Host ''
    & $gh auth login --hostname github.com --git-protocol https --web
    if ($LASTEXITCODE -ne 0) { Warn '授权未完成或未通过,稍后可重跑本脚本。' }
}
Wait-Key

# ─────────────────────────── 4/4  验证 ───────────────────────────
Stage 4 '验证'
$gh = Get-GhPath
Say '-- gh 版本 --'
& $gh --version | ForEach-Object { Say ('  ' + $_) }
Write-Host ''
Say '-- 登录状态 --'
$st = & $gh auth status 2>&1
$st | ForEach-Object { Say ('  ' + $_) }
Write-Host ''
if ($LASTEXITCODE -eq 0) {
    Ok '授权成功,gh 现在可用。'
    Say ''
    Say '常用下一步:'
    Say '  gh repo list                 # 列出你的仓库'
    Say '  gh auth setup-git            # 让 git 也复用 gh 的凭据'
} else {
    Warn '尚未登录。可在任意终端运行:  gh auth login --web'
}

Write-Host ''
Write-Host ('=' * 66) -ForegroundColor DarkCyan
Write-Host '  完成。' -ForegroundColor Cyan
Write-Host ('=' * 66) -ForegroundColor DarkCyan
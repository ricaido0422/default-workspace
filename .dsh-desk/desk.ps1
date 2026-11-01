#requires -Version 5.1
<#
.SYNOPSIS
  Windows desktop automation core (win-desk skill)
.DESCRIPTION
  Zero external dependencies: PowerShell 5.1 + .NET Framework + UI Automation + Win32 P/Invoke.
  Actions: list, tree, find, click, coords, type, key, get, activate, minimize, maximize,
           restore, close, move, screenshot, wait
.NOTES
  Invoke via: powershell.exe -NoProfile -ExecutionPolicy Bypass -File desk.ps1 ...
  This file is intentionally ASCII-only: PowerShell 5.1 reads a BOM-less .ps1 as ANSI.
#>
[CmdletBinding()]
param(
  [Parameter(Mandatory=$true)]
  [ValidateSet('list','tree','find','click','coords','type','key','get','activate','minimize','maximize','restore','close','move','screenshot','wait')]
  [string]$Action,
  [string]$Window,
  [string]$Name,
  [string]$Type,
  [string]$AutomationId,
  [int]$Index = 0,
  [string]$Text,
  [string]$Key,
  [int]$X = 0, [int]$Y = 0, [int]$Width = 0, [int]$Height = 0,
  [int]$Timeout = 5,
  [string]$Out,
  [int]$MaxCount = 300,
  [switch]$All,
  [switch]$NoActivate,
  [switch]$NoClear
)

$ErrorActionPreference = 'Stop'
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}

# ==================== native interop ====================
if (-not ('DeskNative' -as [type])) {
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public class DeskNative {
  [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
  [DllImport("user32.dll")] public static extern bool SetCursorPos(int X, int Y);
  [DllImport("user32.dll")] public static extern void mouse_event(uint dwFlags, int dx, int dy, uint dwData, int dwExtraInfo);
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
  [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
  [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);
  [DllImport("user32.dll")] public static extern bool MoveWindow(IntPtr hWnd, int X, int Y, int nWidth, int nHeight, bool bRepaint);
  [DllImport("user32.dll")] public static extern bool PostMessage(IntPtr hWnd, uint Msg, IntPtr wParam, IntPtr lParam);
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint lpdwProcessId);
  [DllImport("user32.dll")] public static extern bool AttachThreadInput(uint idAttach, uint idAttachTo, bool fAttach);
}
'@
}
[DeskNative]::SetProcessDPIAware() | Out-Null
Add-Type -AssemblyName System.Windows.Forms | Out-Null
Add-Type -AssemblyName System.Drawing | Out-Null
Add-Type -AssemblyName UIAutomationClient | Out-Null
Add-Type -AssemblyName UIAutomationTypes | Out-Null

$RootEl = [System.Windows.Automation.AutomationElement]::RootElement

$TypeAlias = @{
 'button'='Button'; 'edit'='Edit'; 'text'='Text'; 'checkbox'='CheckBox'; 'combobox'='ComboBox'
 'list'='List'; 'listitem'='ListItem'; 'menu'='Menu'; 'menuitem'='MenuItem'; 'tab'='Tab'
 'tabitem'='TabItem'; 'window'='Window'; 'pane'='Pane'; 'hyperlink'='Hyperlink'; 'image'='Image'
 'tree'='Tree'; 'treeitem'='TreeItem'; 'table'='Table'; 'document'='Document'; 'group'='Group'
 'radio'='RadioButton'; 'slider'='Slider'; 'spinner'='Spinner'; 'statusbar'='StatusBar'
 'toolbar'='ToolBar'; 'tooltip'='ToolTip'; 'custom'='Custom'; 'header'='Header'
 'headeritem'='HeaderItem'; 'datagrid'='DataGrid'; 'dataitem'='DataItem'
 'progressbar'='ProgressBar'; 'titlebar'='TitleBar'; 'separator'='Separator'
 'scrollbar'='ScrollBar'; 'thumb'='Thumb'; 'calendar'='Calendar'
}

# ==================== helpers ====================
function Get-Prop {
  param($Element, [string]$Property)
  try { return $Element.Current.$Property } catch { return $null }
}

function Get-CtrlTypeName($Element) {
  $pn = Get-Prop $Element 'ControlType'
  if ($null -eq $pn) { return '' }
  $s = "$pn"
  $s = $s -replace '^.*\.', '' -replace 'ControlType$', ''
  return $s
}

function Get-ElementRect($Element) {
  try {
    $r = $Element.Current.BoundingRectangle
    if ($r.Width -le 0 -or $r.Height -le 0) { return $null }
    return $r
  } catch { return $null }
}

function Test-AllFilters {
  param($Name, $TypeName, $Id)
  if ($Name -and ($Name -notlike "*$Name*")) { return $false }
  if ($Type -and ($TypeName -ne $Type)) { return $false }
  if ($AutomationId -and ($Id -notlike "*$AutomationId*")) { return $false }
  return $true
}

function ConvertTo-SendKeys([string]$s) {
  $sb = New-Object System.Text.StringBuilder
  foreach ($ch in $s.ToCharArray()) {
    if ('+^%(){}~[]'.IndexOf($ch) -ge 0) {
      [void]$sb.Append('{'); [void]$sb.Append($ch); [void]$sb.Append('}')
    } else { [void]$sb.Append($ch) }
  }
  return $sb.ToString()
}

function Click-Point {
  param([int]$CX, [int]$CY, [string]$Button = 'left', [int]$Clicks = 1)
  for ($i = 0; $i -lt $Clicks; $i++) {
    [DeskNative]::SetCursorPos($CX, $CY) | Out-Null
    Start-Sleep -Milliseconds 40
    if ($Button -eq 'right') {
      [DeskNative]::mouse_event(8, 0, 0, 0, 0)
      [DeskNative]::mouse_event(16, 0, 0, 0, 0)
    } else {
      [DeskNative]::mouse_event(2, 0, 0, 0, 0)
      [DeskNative]::mouse_event(4, 0, 0, 0, 0)
    }
    Start-Sleep -Milliseconds 60
  }
}

# ==================== windows ====================
function Get-MatchedWindows {
  param([string]$Pattern)
  $cond = New-Object System.Windows.Automation.PropertyCondition(
      [System.Windows.Automation.AutomationElement]::ControlTypeProperty,
      [System.Windows.Automation.ControlType]::Window)
  $all = $RootEl.FindAll([System.Windows.Automation.TreeScope]::Children, $cond)
  $list = New-Object System.Collections.Generic.List[object]
  foreach ($el in $all) {
    $n = Get-Prop $el 'Name'
    if ([string]::IsNullOrWhiteSpace($n)) { continue }
    if ($n -eq 'Program Manager') { continue }
    $pidv = 0
    try {
      $raw = $el.Current.ProcessId
      if ($null -ne $raw) { $pidv = [int]$raw }
    } catch {}
    $pname = ''
    if ($pidv -gt 0) { try { $pname = (Get-Process -Id $pidv -ErrorAction Stop).ProcessName } catch {} }
    if ($Pattern) {
      $cls = Get-Prop $el 'ClassName'
      if ("$n" -notlike "*$Pattern*" -and "$pname" -notlike "*$Pattern*" -and "$cls" -notlike "*$Pattern*") { continue }
    }
    $hwnd = Get-Prop $el 'NativeWindowHandle'
    $list.Add([pscustomobject]@{
      Name = "$n"; Process = "$pname"; Pid = "$pidv"
      Hwnd = ([int64]$hwnd); Element = $el
    })
  }
  return $list
}

function Select-Window {
  param([string]$Pattern, [int]$Wait = 0)
  $deadline = (Get-Date).AddSeconds([Math]::Max($Wait, 0))
  do {
    $cands = @(Get-MatchedWindows -Pattern $Pattern)
    if ($env:DESK_DEBUG -eq '1') {
      Write-Host "[debug] pattern='$Pattern' candidates=$($cands.Count)"
      foreach ($c in $cands) {
        Write-Host ("[debug]   name='{0}' proc='{1}' pid={2} cls='{3}'" -f $c.Name, $c.Process, $c.Pid, (Get-Prop $c.Element 'ClassName'))
      }
    }
    if ($cands.Count -gt 0) {
      if ($Pattern) {
        $exact = $cands | Where-Object { $_.Name -eq $Pattern } | Select-Object -First 1
        if ($exact) { return $exact }
      }
      return $cands[0]
    }
    if ($Wait -le 0) { break }
    Start-Sleep -Milliseconds 300
  } while ((Get-Date) -lt $deadline)
  return $null
}

function Activate-Window($Element) {
  $hwnd = Get-Prop $Element 'NativeWindowHandle'
  if ($null -eq $hwnd -or $hwnd -eq [IntPtr]::Zero) { return $false }
  $wpid = 0; $wtid = [DeskNative]::GetWindowThreadProcessId($hwnd, [ref]$wpid)
  $fgh = [DeskNative]::GetForegroundWindow()
  $fgpid = 0; $fgtid = 0
  if ($fgh -ne [IntPtr]::Zero) { $fgtid = [DeskNative]::GetWindowThreadProcessId($fgh, [ref]$fgpid) }
  $wp = $null
  try { $wp = $Element.GetCurrentPattern([System.Windows.Automation.WindowPattern]::Pattern) } catch {}
  if ($wp) {
    try { if ("$($wp.Current.WindowVisualState)" -eq 'Minimized') { $wp.SetWindowVisualState([System.Windows.Automation.WindowVisualState]::Normal) } } catch {}
  }
  if ($wtid -ne $fgtid -and $fgtid -ne 0) { [DeskNative]::AttachThreadInput($fgtid, $wtid, $true) | Out-Null }
  [DeskNative]::SetForegroundWindow($hwnd) | Out-Null
  Start-Sleep -Milliseconds 150
  if ($wtid -ne $fgtid -and $fgtid -ne 0) { [DeskNative]::AttachThreadInput($fgtid, $wtid, $false) | Out-Null }
  return $true
}

# ==================== controls ====================
function Get-Descendants {
  param($Root, [int]$MaxDepth = 12, [int]$MaxCount = 4000, [switch]$IncludeRoot)
  $out = New-Object 'System.Collections.Generic.List[object]'
  # NOTE: use a generic List as the stack. A bare System.Collections.Stack fails type
  # resolution here because the UIA/WinForms Add-Type calls above load conflicting
  # System.Collections facades into this runspace.
  $stack = New-Object 'System.Collections.Generic.List[object]'
  $stack.Add(@{ El = $Root; Depth = 0 })
  $cond = [System.Windows.Automation.Condition]::TrueCondition
  while ($stack.Count -gt 0 -and $out.Count -lt $MaxCount) {
    $cur = $stack[$stack.Count - 1]; $stack.RemoveAt($stack.Count - 1)
    $el = $cur.El; $d = $cur.Depth
    $children = $null
    try { $children = $el.FindAll([System.Windows.Automation.TreeScope]::Children, $cond) } catch { $children = $null }
    if ($null -eq $children) { continue }
    foreach ($c in $children) {
      $cn = Get-Prop $c 'Name'; if ($null -eq $cn) { $cn = '' }
      $ctn = Get-CtrlTypeName $c
      $cid = Get-Prop $c 'AutomationId'; if ($null -eq $cid) { $cid = '' }
      if (Test-AllFilters -Name "$cn" -TypeName "$ctn" -Id "$cid") {
        $r = Get-ElementRect $c
        $out.Add([pscustomobject]@{
          Name = "$cn"; Type = "$ctn"; Id = "$cid"; Depth = ($d + 1)
          Off = "$(Get-Prop $c 'IsOffscreen')"
          X = $(if ($r) { [int]$r.X } else { 0 }); Y = $(if ($r) { [int]$r.Y } else { 0 })
          W = $(if ($r) { [int]$r.Width } else { 0 }); H = $(if ($r) { [int]$r.Height } else { 0 })
          Element = $c
        })
      }
      if ($d + 1 -lt $MaxDepth) { $stack.Add(@{ El = $c; Depth = ($d + 1) }) }
    }
  }
  return $out
}

function Find-Controls {
  param($WindowElement, [int]$Wait = 0)
  if (-not $Name -and -not $Type -and -not $AutomationId) {
    throw "find/click/coords/type needs at least one locator: -Name / -Type / -AutomationId"
  }
  $resolvedType = $null
  if ($Type) {
    $key = "$Type".ToLower()
    if ($TypeAlias.ContainsKey($key)) { $resolvedType = $TypeAlias[$key] } else { $resolvedType = $Type }
  }
  $deadline = (Get-Date).AddSeconds([Math]::Max($Wait, 0))
  do {
    $found = Get-Descendants -Root $WindowElement -MaxCount 4000 |
      Where-Object {
        ($resolvedType -eq $null -or $_.Type -eq $resolvedType) -and
        (-not $Name -or $_.Name -like "*$Name*") -and
        (-not $AutomationId -or $_.Id -like "*$AutomationId*")
      }
    if ($found -and $found.Count -gt 0) { return $found }
    if ($Wait -le 0) { break }
    Start-Sleep -Milliseconds 300
  } while ((Get-Date) -lt $deadline)
  return $null
}

function Invoke-Element($Element) {
  $patterns = @(
    @{ P = [System.Windows.Automation.InvokePattern]::Pattern;        M = 'Invoke' }
    @{ P = [System.Windows.Automation.SelectionItemPattern]::Pattern; M = 'Select' }
    @{ P = [System.Windows.Automation.TogglePattern]::Pattern;        M = 'Toggle' }
    @{ P = [System.Windows.Automation.ExpandCollapsePattern]::Pattern;M = 'Expand' }
  )
  foreach ($pp in $patterns) {
    try {
      $inst = $Element.GetCurrentPattern($pp.P)
      if ($inst) { $inst.$($pp.M)(); return 'pattern:' + $pp.M }
    } catch {}
  }
  try {
    $la = $Element.GetCurrentPattern([System.Windows.Automation.LegacyIAccessiblePattern]::Pattern)
    if ($la) { $la.DoDefaultAction(); return 'legacy-default-action' }
  } catch {}
  $r = Get-ElementRect $Element
  if ($r) {
    $cx = [int]($r.X + $r.Width / 2); $cy = [int]($r.Y + $r.Height / 2)
    Click-Point -CX $cx -CY $cy
    return 'coord-click'
  }
  return $null
}

function Set-ElementText($Element, [string]$Value) {
  $vp = $null
  try { $vp = $Element.GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern) } catch {}
  if ($vp) {
    try { if (-not $vp.Current.IsReadOnly) { $vp.SetValue($Value); return 'value-pattern' } } catch {}
  }
  try { $Element.SetFocus() } catch {}
  Start-Sleep -Milliseconds 120
  if (-not $NoClear) { [System.Windows.Forms.SendKeys]::SendWait('^a') }
  Start-Sleep -Milliseconds 60
  [System.Windows.Forms.SendKeys]::SendWait((ConvertTo-SendKeys $Value))
  return 'sendkeys'
}

function Get-ElementText($Element) {
  $vp = $null
  try { $vp = $Element.GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern) } catch {}
  if ($vp) { try { $t = $vp.Current.Value; if ($t) { return "$t" } } catch {} }
  $tp = $null
  try { $tp = $Element.GetCurrentPattern([System.Windows.Automation.TextPattern]::Pattern) } catch {}
  if ($tp) { try { return "$($tp.DocumentRange.GetText(-1))" } catch {} }
  return "$(Get-Prop $Element 'Name')"
}

function Save-Screenshot {
  param([int]$SX, [int]$SY, [int]$SW, [int]$SH, [string]$Path)
  if ($SW -le 0 -or $SH -le 0) {
    $b = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds
    $SX = $b.X; $SY = $b.Y; $SW = $b.Width; $SH = $b.Height
  }
  $bmp = New-Object System.Drawing.Bitmap($SW, $SH)
  $g = [System.Drawing.Graphics]::FromImage($bmp)
  try { $g.CopyFromScreen($SX, $SY, 0, 0, $bmp.Size, [System.Drawing.CopyPixelOperation]::SourceCopy) } finally { $g.Dispose() }
  $dir = Split-Path -Parent $Path
  if ($dir -and -not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
  $bmp.Save($Path, [System.Drawing.Imaging.ImageFormat]::Png)
  $bmp.Dispose()
}

# ==================== dispatch ====================
$win = $null
if ($Window) {
  $win = Select-Window -Pattern $Window -Wait $Timeout
  if (-not $win) { Write-Error "window not found: $Window"; exit 2 }
}

switch ($Action) {
  'list' {
    $wins = Get-MatchedWindows -Pattern $Window
    if (-not $wins -or $wins.Count -eq 0) { Write-Output "(no matching windows)"; break }
    $i = 0
    foreach ($w in $wins) {
      Write-Output ("[{0}] {1}  (proc={2}, pid={3}, hwnd=0x{4:X})" -f $i, $w.Name, $w.Process, $w.Pid, $w.Hwnd)
      $i++
    }
  }
  'tree' {
    if (-not $win) { Write-Error "tree needs -Window"; exit 2 }
    $els = Get-Descendants -Root $win.Element -MaxCount $(if ($All) { 4000 } else { $MaxCount })
    if (-not $els -or $els.Count -eq 0) { Write-Output "(no controls found)"; break }
    foreach ($e in $els) {
      $ind = ' ' * [Math]::Min($e.Depth * 2, 24)
      $pos = ''
      if ($e.W -gt 0) { $pos = " @$($e.X),$($e.Y) $($e.W)x$($e.H)" }
      $nm = if ($e.Name) { " '$($e.Name)'" } else { '' }
      $id = if ($e.Id) { " #$($e.Id)" } else { '' }
      Write-Output ("{0}- {1}{2}{3}{4}" -f $ind, $e.Type, $nm, $id, $pos)
    }
    Write-Output "(total: $($els.Count))"
  }
  'find' {
    $els = Find-Controls -WindowElement $win.Element -Wait $Timeout
    if (-not $els) { Write-Output "(no match)"; exit 3 }
    $i = 0
    foreach ($e in $els) {
      $idpart = if ($e.Id) { " #$($e.Id)" } else { '' }
      $offpart = if ($e.Off -eq 'True') { ' [offscreen]' } else { '' }
      Write-Output ("[{0}] {1} '{2}'{3} @{4},{5} {6}x{7}{8}" -f $i, $e.Type, $e.Name, $idpart, $e.X, $e.Y, $e.W, $e.H, $offpart)
      $i++
    }
  }
  'coords' {
    $els = Find-Controls -WindowElement $win.Element -Wait $Timeout
    if (-not $els) { Write-Output "(no match)"; exit 3 }
    $e = $els[$Index]
    $cx = [int]($e.X + $e.W / 2); $cy = [int]($e.Y + $e.H / 2)
    Write-Output "center=$cx,$cy rect=$($e.X),$($e.Y),$($e.W),$($e.H)"
  }
  'click' {
    $els = Find-Controls -WindowElement $win.Element -Wait $Timeout
    if (-not $els) { Write-Output "(no match)"; exit 3 }
    $e = $els[$Index]
    if (-not $NoActivate) { Activate-Window $win.Element | Out-Null; Start-Sleep -Milliseconds 100 }
    $how = Invoke-Element $e.Element
    Write-Output "clicked: $($e.Type) '$($e.Name)' via $how"
  }
  'type' {
    if ($Window -and -not $NoActivate) { Activate-Window $win.Element | Out-Null; Start-Sleep -Milliseconds 100 }
    if ($Name -or $AutomationId) {
      $els = Find-Controls -WindowElement $win.Element -Wait $Timeout
      if (-not $els) { Write-Output "(no match)"; exit 3 }
      $e = $els[$Index]
      $how = Set-ElementText -Element $e.Element -Value $Text
      Write-Output "typed into $($e.Type) '$($e.Name)' via $how"
    } else {
      if (-not $NoClear) { [System.Windows.Forms.SendKeys]::SendWait('^a') }
      Start-Sleep -Milliseconds 60
      [System.Windows.Forms.SendKeys]::SendWait((ConvertTo-SendKeys $Text))
      Write-Output "typed into foreground window via sendkeys"
    }
  }
  'key' {
    if ($Window -and -not $NoActivate) { Activate-Window $win.Element | Out-Null; Start-Sleep -Milliseconds 100 }
    [System.Windows.Forms.SendKeys]::SendWait($Key)
    Write-Output "sent key: $Key"
  }
  'get' {
    if ($Name -or $AutomationId) {
      $els = Find-Controls -WindowElement $win.Element -Wait $Timeout
      if (-not $els) { Write-Output "(no match)"; exit 3 }
      Write-Output (Get-ElementText $els[$Index].Element)
    } else {
      $els = Get-Descendants -Root $win.Element -MaxCount 4000 |
        Where-Object { $_.Name -and $_.Type -in @('Text','Edit','Document','DataItem','ListItem','TreeItem','HeaderItem') }
      $txt = ($els | ForEach-Object { $_.Name }) -join "`n"
      Write-Output $txt
    }
  }
  'activate'  { Activate-Window $win.Element | Out-Null; Write-Output "activated: $($win.Name)" }
  'minimize'  { $h = $win.Element.Current.NativeWindowHandle; [DeskNative]::ShowWindow($h, 1) | Out-Null; Write-Output "minimized: $($win.Name)" }
  'maximize'  { $h = $win.Element.Current.NativeWindowHandle; [DeskNative]::ShowWindow($h, 3) | Out-Null; Write-Output "maximized: $($win.Name)" }
  'restore'   { $h = $win.Element.Current.NativeWindowHandle; [DeskNative]::ShowWindow($h, 9) | Out-Null; Write-Output "restored: $($win.Name)" }
  'close'     {
    $closed = $false
    try { $wp = $win.Element.GetCurrentPattern([System.Windows.Automation.WindowPattern]::Pattern); if ($wp) { $wp.Close(); $closed = $true } } catch {}
    if (-not $closed) { $h = $win.Element.Current.NativeWindowHandle; [DeskNative]::PostMessage($h, 0x0010, [IntPtr]::Zero, [IntPtr]::Zero) | Out-Null }
    Write-Output "closed: $($win.Name)"
  }
  'move' {
    $h = $win.Element.Current.NativeWindowHandle
    if ($X -eq 0 -and $Y -eq 0 -and $Width -eq 0 -and $Height -eq 0) { Write-Error "move needs -X/-Y/-Width/-Height"; exit 2 }
    $r = Get-ElementRect $win.Element
    if (-not $r) { Write-Error "cannot read window rect"; exit 3 }
    $nx = if ($X -ne 0) { $X } else { [int]$r.X }
    $ny = if ($Y -ne 0) { $Y } else { [int]$r.Y }
    $nw = if ($Width -ne 0) { $Width } else { [int]$r.Width }
    $nh = if ($Height -ne 0) { $Height } else { [int]$r.Height }
    [DeskNative]::MoveWindow($h, $nx, $ny, $nw, $nh, $true) | Out-Null
    Write-Output ("moved: {0} -> {1},{2} {3}x{4}" -f $win.Name, $nx, $ny, $nw, $nh)
  }
  'screenshot' {
    if (-not $Out) { Write-Error "screenshot needs -Out <path.png>"; exit 2 }
    if ($win) {
      $r = Get-ElementRect $win.Element
      if (-not $r) { Write-Error "cannot read window rect (window may be minimized)"; exit 3 }
      if ($r.X -eq 0 -and $r.Y -eq 0) {
        $b = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds
        Save-Screenshot -SX $b.X -SY $b.Y -SW $b.Width -SH $b.Height -Path $Out
      } else {
        Save-Screenshot -SX ([int]$r.X) -SY ([int]$r.Y) -SW ([int]$r.Width) -SH ([int]$r.Height) -Path $Out
      }
      Write-Output "saved: $Out"
    } else {
      $b = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds
      Save-Screenshot -SX $b.X -SY $b.Y -SW $b.Width -SH $b.Height -Path $Out
      Write-Output "saved (fullscreen): $Out"
    }
  }
  'wait' {
    if ($Window) {
      $w = Select-Window -Pattern $Window -Wait ([Math]::Max($Timeout,1))
      if ($w) { Write-Output "window ready: $($w.Name)" } else { Write-Output "timeout: window '$Window' not found"; exit 3 }
    } else {
      $els = Find-Controls -WindowElement $win.Element -Wait ([Math]::Max($Timeout,1))
      if ($els) { Write-Output "control ready: $($els[0].Type) '$($els[0].Name)'" } else { Write-Output "timeout: control not found"; exit 3 }
    }
  }
}

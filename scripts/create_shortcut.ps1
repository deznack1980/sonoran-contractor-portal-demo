# Create CorridorIQ HQ desktop + Start Menu shortcuts
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Launcher = Join-Path $Root "CorridorIQHQ.bat"
$Icon = Join-Path $Root "assets\corridoriq.ico"

if (-not (Test-Path $Launcher)) {
  Write-Host "Missing CorridorIQHQ.bat at $Launcher"
  exit 1
}

$Wsh = New-Object -ComObject WScript.Shell

function New-CorridorIQShortcut($path) {
  $sc = $Wsh.CreateShortcut($path)
  $sc.TargetPath = $Launcher
  $sc.WorkingDirectory = $Root
  $sc.WindowStyle = 1
  $sc.Description = "CorridorIQ - Secure portal sign-in"
  if (Test-Path $Icon) { $sc.IconLocation = "$Icon,0" }
  $sc.Save()
  Write-Host "Created: $path"
}

$Desktop = [Environment]::GetFolderPath("Desktop")
$StartMenu = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs"
New-CorridorIQShortcut (Join-Path $Desktop "CorridorIQ HQ.lnk")
New-CorridorIQShortcut (Join-Path $StartMenu "CorridorIQ HQ.lnk")

Write-Host ""
Write-Host "Pin to taskbar: launch CorridorIQ HQ, then right-click the taskbar icon -> Pin to taskbar."

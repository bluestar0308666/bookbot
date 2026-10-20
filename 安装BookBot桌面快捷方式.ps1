$ErrorActionPreference = "Stop"
$ProjectRoot = $PSScriptRoot
$Launcher = Join-Path $ProjectRoot "tools\start_desktop_app.vbs"
$WScript = Join-Path $env:SystemRoot "System32\wscript.exe"
$Desktop = [Environment]::GetFolderPath("Desktop")
$ShortcutPath = Join-Path $Desktop "BookBot.lnk"
$Shell = New-Object -ComObject WScript.Shell
$Shortcut = $Shell.CreateShortcut($ShortcutPath)
$Shortcut.TargetPath = $WScript
$Shortcut.Arguments = "`"$Launcher`""
$Shortcut.WorkingDirectory = $ProjectRoot
$Shortcut.Description = "BookBot personal reading workspace"
$Edge = "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
if (Test-Path -LiteralPath $Edge) { $Shortcut.IconLocation = "$Edge,0" }
$Shortcut.Save()
Write-Host "BookBot shortcut created: $ShortcutPath"

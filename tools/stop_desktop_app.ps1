$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$PidFile = Join-Path $ProjectRoot "data\bookbot-desktop.pid"

if (-not (Test-Path -LiteralPath $PidFile)) {
  Write-Host "BookBot is not running."
  exit 0
}

$bookbotPid = [int](Get-Content -LiteralPath $PidFile -Raw)
$process = Get-Process -Id $bookbotPid -ErrorAction SilentlyContinue
if (-not $process) {
  Remove-Item -LiteralPath $PidFile -Force
  Write-Host "BookBot is already stopped."
  exit 0
}
if ($process.ProcessName -notlike "python*") {
  throw "Safety check failed: the recorded process is not a BookBot Python service."
}

Stop-Process -Id $bookbotPid
Remove-Item -LiteralPath $PidFile -Force
Write-Host "BookBot background service stopped."

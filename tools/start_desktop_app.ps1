$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$PythonConsole = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$PythonWindow = Join-Path $ProjectRoot ".venv\Scripts\pythonw.exe"
$Python = if (Test-Path -LiteralPath $PythonWindow) { $PythonWindow } else { $PythonConsole }
$Server = Join-Path $ProjectRoot "server.py"
$Url = "http://127.0.0.1:4173/?desktop=1&v=1.0.4"
$PidFile = Join-Path $ProjectRoot "data\bookbot-desktop.pid"
$OutputLog = Join-Path $ProjectRoot "data\bookbot-desktop.out.log"
$ErrorLog = Join-Path $ProjectRoot "data\bookbot-desktop.error.log"
$EdgeProfile = Join-Path $ProjectRoot "data\edge-profile"

if (-not (Test-Path -LiteralPath $PythonConsole)) {
  throw 'BookBot local runtime is not ready. Run the setup launcher once.'
}

$ready = $false
try {
  $response = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:4173/api/status" -TimeoutSec 2
  $ready = $response.StatusCode -eq 200
} catch {}

if (-not $ready) {
  New-Item -ItemType Directory -Path (Split-Path -Parent $PidFile) -Force | Out-Null
  if (Test-Path -LiteralPath $PidFile) {
    $stalePid = [int](Get-Content -LiteralPath $PidFile -Raw)
    $staleProcess = Get-Process -Id $stalePid -ErrorAction SilentlyContinue
    if ($staleProcess -and $staleProcess.ProcessName -like "python*") {
      Stop-Process -Id $stalePid -Force
      Start-Sleep -Milliseconds 300
    }
    Remove-Item -LiteralPath $PidFile -Force
  }
  Set-Content -LiteralPath $OutputLog -Value "" -Encoding utf8
  Set-Content -LiteralPath $ErrorLog -Value "" -Encoding utf8
  $serverProcess = Start-Process -FilePath $Python -ArgumentList @($Server, "--desktop", "--no-browser") -WorkingDirectory $ProjectRoot -WindowStyle Hidden -RedirectStandardOutput $OutputLog -RedirectStandardError $ErrorLog -PassThru
  Set-Content -LiteralPath $PidFile -Value $serverProcess.Id -Encoding ascii
  for ($attempt = 0; $attempt -lt 40; $attempt += 1) {
    Start-Sleep -Milliseconds 250
    if ($serverProcess.HasExited) { break }
    try {
      $response = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:4173/api/status" -TimeoutSec 1
      if ($response.StatusCode -eq 200) { $ready = $true; break }
    } catch {}
  }
}

if (-not $ready) {
  Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue
  $detail = if (Test-Path -LiteralPath $ErrorLog) { (Get-Content -LiteralPath $ErrorLog -Tail 8) -join "`n" } else { "" }
  if (-not $detail) { $detail = "The background service did not listen on port 4173." }
  throw "BookBot failed to start: $detail"
}

$edgeCandidates = @(
  "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
  "C:\Program Files\Microsoft\Edge\Application\msedge.exe"
)
$edge = $edgeCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if ($edge) {
  New-Item -ItemType Directory -Path $EdgeProfile -Force | Out-Null
  Start-Process -FilePath $edge -ArgumentList @("--user-data-dir=$EdgeProfile", "--no-first-run", "--new-window", "--app=$Url", "--start-maximized") | Out-Null
} else {
  Start-Process $Url | Out-Null
}

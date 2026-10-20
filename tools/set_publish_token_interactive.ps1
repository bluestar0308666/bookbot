param([string]$Token = "", [switch]$NoPause)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$ConfigPath = Join-Path $ProjectRoot "data\publish-config.json"
$ServerBase = if ($env:BOOKBOT_SERVER_BASE) { $env:BOOKBOT_SERVER_BASE.TrimEnd('/') } else { "http://127.0.0.1:3011" }

Write-Host ""
Write-Host "BookBot 发布令牌设置" -ForegroundColor Cyan
Write-Host "---------------------"
Write-Host "1. 在服务器终端运行："
Write-Host "   sudo sed -n 's/^BOOKBOT_PUBLISH_TOKEN=//p' /etc/bookbot.env | tr -d '\r\n'"
Write-Host "2. 只复制输出的 64 位令牌"
Write-Host "3. 粘贴到下面，按回车"
Write-Host ""

if (-not $Token) {
  $Token = Read-Host "粘贴发布令牌"
}
$Token = $Token.Trim()
if ($Token -match '^BOOKBOT_PUBLISH_TOKEN\s*=\s*(.+)$') {
  $Token = $Matches[1].Trim()
}
$Token = $Token.Trim('"').Trim("'")

if ($Token -notmatch '^[0-9a-fA-F]{64}$') {
  Write-Host ""
  Write-Host "令牌格式不对：需要正好 64 位十六进制字符。" -ForegroundColor Red
  Write-Host "请重新运行，只复制 BOOKBOT_PUBLISH_TOKEN= 后面的值。" -ForegroundColor Yellow
  exit 1
}

if (-not (Test-Path -LiteralPath $ConfigPath)) {
  throw "找不到配置：$ConfigPath"
}

$config = Get-Content -LiteralPath $ConfigPath -Raw -Encoding UTF8 | ConvertFrom-Json
$config.serverBase = $ServerBase
$config.publishToken = $Token
if (-not $config.chunkBytes) { $config.chunkBytes = 4194304 }

$json = $config | ConvertTo-Json
[System.IO.File]::WriteAllText($ConfigPath, $json, (New-Object System.Text.UTF8Encoding($false)))

$sha = [Security.Cryptography.SHA256]::Create()
$hash = [BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($Token))).Replace('-', '').ToLower()

Write-Host ""
Write-Host "已写入：$ConfigPath" -ForegroundColor Green
Write-Host "服务器：$ServerBase"
Write-Host "令牌长度：$($Token.Length)"
Write-Host "令牌 SHA256：$hash"
Write-Host ""

Write-Host "正在测试发布接口..." -ForegroundColor Cyan
try {
  $response = Invoke-WebRequest -UseBasicParsing -Uri "$ServerBase/api/publish/library" -Headers @{ Authorization = "Bearer $Token" } -TimeoutSec 15
  Write-Host "接口验证成功：HTTP $($response.StatusCode)" -ForegroundColor Green
  Write-Host "线上发布接口已连通。" -ForegroundColor Green
} catch {
  $status = $null
  try { $status = [int]$_.Exception.Response.StatusCode } catch {}
  Write-Host "接口验证失败：HTTP $status" -ForegroundColor Red
  if ($_.ErrorDetails.Message) { Write-Host $_.ErrorDetails.Message -ForegroundColor Yellow }
  Write-Host ""
  Write-Host "如果显示“发布令牌无效”，说明令牌仍不一致。" -ForegroundColor Yellow
  Write-Host "如果显示 401 且要求 Basic Auth，说明 Caddy 还没放行 /api/publish/。" -ForegroundColor Yellow
  Write-Host "把上面的状态和返回内容发给我即可。" -ForegroundColor Yellow
}

Write-Host ""
if (-not $NoPause) { Read-Host "按回车关闭" }

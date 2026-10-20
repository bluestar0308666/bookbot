param([string]$Token = "")

$ErrorActionPreference = "Stop"

$configPath = Join-Path (Split-Path -Parent $PSScriptRoot) "data\publish-config.json"
if (-not (Test-Path -LiteralPath $configPath)) {
  throw "找不到发布配置：$configPath"
}

$token = if ($Token) { $Token.Trim() } else { (Get-Clipboard -Raw).Trim() }
if ($token -match '^BOOKBOT_PUBLISH_TOKEN\s*=\s*(.+)$') {
  $token = $Matches[1].Trim()
}
$token = $token.Trim('"').Trim("'")

if ($token -notmatch '^[0-9a-fA-F]{64}$') {
  throw "剪贴板里不是有效的发布令牌。请只复制 BOOKBOT_PUBLISH_TOKEN= 后面的 64 位十六进制值，不要复制整段命令或代码块。"
}

$config = Get-Content -LiteralPath $configPath -Raw -Encoding UTF8 | ConvertFrom-Json
$config.publishToken = $token
$config.serverBase = if ($env:BOOKBOT_SERVER_BASE) {
  $env:BOOKBOT_SERVER_BASE.TrimEnd('/')
} elseif ($config.serverBase) {
  ([string]$config.serverBase).TrimEnd('/')
} else {
  "http://127.0.0.1:3011"
}
if (-not $config.chunkBytes) { $config.chunkBytes = 4194304 }

$json = $config | ConvertTo-Json
[System.IO.File]::WriteAllText($configPath, $json, (New-Object System.Text.UTF8Encoding($false)))

$sha = [Security.Cryptography.SHA256]::Create()
$hash = [BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($token))).Replace('-', '').ToLower()

Write-Host "已写入发布配置：$configPath"
Write-Host "令牌长度：$($token.Length)"
Write-Host "令牌 SHA256：$hash"
Write-Host "服务器地址：$($config.serverBase)"

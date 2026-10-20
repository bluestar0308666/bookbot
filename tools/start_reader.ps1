param(
  [ValidateSet("workspace", "bot")]
  [string]$Mode = "workspace"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$VenvRoot = Join-Path $ProjectRoot ".venv"
$VenvPython = Join-Path $VenvRoot "Scripts\python.exe"
$Requirements = Join-Path $ProjectRoot "requirements.txt"

function Find-BasePython {
  $py = Get-Command py -ErrorAction SilentlyContinue
  if ($py) { return @{ Command = $py.Source; Prefix = @("-3") } }
  $python = Get-Command python -ErrorAction SilentlyContinue
  if ($python) { return @{ Command = $python.Source; Prefix = @() } }
  throw "没有找到 Python 3。请先安装 Python 3，再重新双击启动。"
}

if (-not (Test-Path -LiteralPath $VenvPython)) {
  Write-Host "首次启动：正在为“一页”准备独立运行环境……"
  $base = Find-BasePython
  & $base.Command @($base.Prefix) -m venv --without-pip $VenvRoot
  if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $VenvPython)) {
    throw "无法创建阅读器运行环境。"
  }
}

& $VenvPython -c "import pypdf, PIL" 2>$null
if ($LASTEXITCODE -ne 0) {
  Write-Host "正在安装 PDF 处理组件（只在第一次需要）……"
  $base = Find-BasePython
  $sitePackages = Join-Path $VenvRoot "Lib\site-packages"
  New-Item -ItemType Directory -Path $sitePackages -Force | Out-Null
  & $base.Command @($base.Prefix) -m pip install --disable-pip-version-check --target $sitePackages -r $Requirements
  if ($LASTEXITCODE -ne 0) { throw "PDF 处理组件安装失败，请检查网络后重试。" }
}

Set-Location $ProjectRoot
if ($Mode -eq "bot") {
  New-Item -ItemType Directory -Path (Join-Path $ProjectRoot "待处理书籍") -Force | Out-Null
  Write-Host "书籍助手已经启动。把 PDF 放入“待处理书籍”文件夹即可。"
  & $VenvPython (Join-Path $ProjectRoot "tools\book_bot.py") --watch
} else {
  Write-Host "正在启动“一页”个人图书馆……"
  Write-Host "书架、阅读器和书籍处理助手将一起运行。"
  & $VenvPython (Join-Path $ProjectRoot "server.py") --desktop
}

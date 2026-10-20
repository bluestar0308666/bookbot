param(
  [Parameter(Mandatory = $true)][string]$PdfPath,
  [Parameter(Mandatory = $true)][string]$OutputDir,
  [Parameter(Mandatory = $true)][string]$Pages,
  [int]$Width = 1200
)

$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Runtime.WindowsRuntime
[Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime] | Out-Null
[Windows.Data.Pdf.PdfDocument, Windows.Data.Pdf, ContentType = WindowsRuntime] | Out-Null
[Windows.Storage.Streams.InMemoryRandomAccessStream, Windows.Storage.Streams, ContentType = WindowsRuntime] | Out-Null
[Windows.Data.Pdf.PdfPageRenderOptions, Windows.Data.Pdf, ContentType = WindowsRuntime] | Out-Null

$asTaskGeneric = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
  $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
})[0]
$asTaskAction = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
  $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncAction'
})[0]

function Await-Operation($op, [Type]$type) {
  $task = $asTaskGeneric.MakeGenericMethod($type).Invoke($null, @($op))
  $task.Wait(-1) | Out-Null
  return $task.Result
}
function Await-Action($action) {
  $task = $asTaskAction.Invoke($null, @($action))
  $task.Wait(-1) | Out-Null
}

New-Item -ItemType Directory -Path $OutputDir -Force | Out-Null
$file = Await-Operation ([Windows.Storage.StorageFile]::GetFileFromPathAsync($PdfPath)) ([Windows.Storage.StorageFile])
$doc = Await-Operation ([Windows.Data.Pdf.PdfDocument]::LoadFromFileAsync($file)) ([Windows.Data.Pdf.PdfDocument])

$failed = 0
foreach ($raw in $Pages.Split(',')) {
  $number = 0
  if (-not [int]::TryParse($raw.Trim(), [ref]$number) -or $number -lt 1) { continue }
  try {
    $page = $doc.GetPage($number - 1)
    $stream = [Windows.Storage.Streams.InMemoryRandomAccessStream]::new()
    $options = [Windows.Data.Pdf.PdfPageRenderOptions]::new()
    $options.DestinationWidth = [uint32]$Width
    Await-Action ($page.RenderToStreamAsync($stream, $options))
    $stream.Seek(0)
    $net = [System.IO.WindowsRuntimeStreamExtensions]::AsStreamForRead($stream)
    $target = Join-Path $OutputDir ("page-{0}.png" -f $number)
    $out = [System.IO.File]::Create($target)
    $net.CopyTo($out)
    $out.Dispose()
    $net.Dispose()
    $stream.Dispose()
    Write-Host ("rendered page {0}" -f $number)
  } catch {
    $failed += 1
    Write-Error ("page {0} failed: {1}" -f $number, $_.Exception.Message)
  }
}
if ($failed -gt 0) { exit 1 }

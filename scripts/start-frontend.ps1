param([ValidateRange(1, 65535)][int]$BackendPort = 8000)
. "$PSScriptRoot\common.ps1"
$env:API_PROXY_TARGET = "http://127.0.0.1:$BackendPort"
Set-Location -LiteralPath (Join-Path $ProjectRoot 'frontend')
Invoke-Checked 'npm' @('run', 'dev')

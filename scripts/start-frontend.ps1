. "$PSScriptRoot\common.ps1"
Set-Location -LiteralPath (Join-Path $ProjectRoot 'frontend')
Invoke-Checked 'npm' @('run', 'dev')

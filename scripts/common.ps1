$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $ProjectRoot
$env:UV_CACHE_DIR = Join-Path $ProjectRoot '.cache\uv'
$env:UV_PYTHON_INSTALL_DIR = Join-Path $ProjectRoot '.runtime\python'
$env:UV_PYTHON_BIN_DIR = Join-Path $ProjectRoot '.runtime\bin'
$env:PYTHONUTF8 = '1'

function Invoke-Checked {
    param([string]$Command, [string[]]$Arguments)
    & $Command @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$Command 执行失败，退出码 $LASTEXITCODE" }
}

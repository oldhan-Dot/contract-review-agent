param([ValidateRange(1, 65535)][int]$Port = 8000)
. "$PSScriptRoot\common.ps1"
Invoke-Checked 'uv' @('run', '--locked', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', "$Port", '--reload')

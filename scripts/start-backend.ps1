. "$PSScriptRoot\common.ps1"
Invoke-Checked 'uv' @('run', '--locked', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', '8000', '--reload')

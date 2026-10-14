. "$PSScriptRoot\common.ps1"
Invoke-Checked 'uv' @('run', '--locked', 'python', '-m', 'app.cli', 'check-env')

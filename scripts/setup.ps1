param([switch]$SkipDatabase)
. "$PSScriptRoot\common.ps1"

foreach ($command in @('uv', 'node', 'npm')) {
    if (-not (Get-Command $command -ErrorAction SilentlyContinue)) {
        throw "缺少 $command，请先安装。"
    }
}
if (-not (Test-Path -LiteralPath '.env')) {
    $template = Get-Content -Raw -Encoding UTF8 '.env.example'
    $password = [Convert]::ToHexString([Security.Cryptography.RandomNumberGenerator]::GetBytes(24))
    $rootPassword = [Convert]::ToHexString([Security.Cryptography.RandomNumberGenerator]::GetBytes(24))
    $template = [regex]::Replace($template, '(?m)^MYSQL_PASSWORD=\r?$', "MYSQL_PASSWORD=$password")
    $template = [regex]::Replace($template, '(?m)^MYSQL_ROOT_PASSWORD=\r?$', "MYSQL_ROOT_PASSWORD=$rootPassword")
    [IO.File]::WriteAllText((Join-Path $ProjectRoot '.env'), $template)
    Write-Host '.env 已创建，数据库密码已随机生成。请自行填写模型与 MinerU 配置。'
}
Invoke-Checked 'uv' @('python', 'install', '3.11', '--no-bin')
Invoke-Checked 'uv' @('sync', '--locked')
Push-Location 'frontend'
try { Invoke-Checked 'npm' @('ci') } finally { Pop-Location }
if (-not $SkipDatabase) {
    Invoke-Checked 'docker' @('compose', 'up', '-d', '--wait', '--wait-timeout', '180', 'mysql')
    Invoke-Checked 'uv' @('run', '--locked', 'python', '-m', 'app.cli', 'init-db')
}

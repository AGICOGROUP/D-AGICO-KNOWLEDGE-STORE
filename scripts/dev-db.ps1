param([ValidateSet('start','stop','status')][string]$Action = 'status')
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$pgBin = Join-Path $projectRoot '.local/postgres/Library/bin'
$pgData = Join-Path $projectRoot '.local/pgdata'
$pgLog = Join-Path $projectRoot '.local/postgres.log'
$layoutFile = Join-Path $projectRoot '.local/test-layout.json'
if (Test-Path -LiteralPath $layoutFile) {
    $layout = Get-Content -LiteralPath $layoutFile -Raw -Encoding UTF8 | ConvertFrom-Json
    $pgData = $layout.postgres
    if ($layout.postgres_launch) { $pgData = $layout.postgres_launch }
    $pgLog = Join-Path $layout.logs 'postgres.log'
    if (-not [IO.Path]::IsPathRooted($pgData) -or -not (Test-Path -LiteralPath (Join-Path $pgData 'PG_VERSION'))) {
        throw 'Configured PostgreSQL directory is not initialized.'
    }
}
$originalPath = $env:PATH
try {
    $env:PATH = "$pgBin;$originalPath"
    if ($Action -eq 'start') {
        & "$pgBin/pg_ctl.exe" -D $pgData -l $pgLog -o '-h 127.0.0.1 -p 15432' -w start
    } elseif ($Action -eq 'stop') {
        & "$pgBin/pg_ctl.exe" -D $pgData -m fast -w stop
    } else {
        & "$pgBin/pg_ctl.exe" -D $pgData status
    }
    if ($LASTEXITCODE -ne 0) { throw 'Development PostgreSQL command failed.' }
} finally { $env:PATH = $originalPath }

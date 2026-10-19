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
        # After an unclean shutdown pg_ctl can warn on stderr while successfully recovering.
        # Launch it with file-backed streams so PowerShell does not turn that warning into a
        # terminating error or wait on handles inherited by the long-running server.
        $ctlOut = Join-Path $projectRoot '.local/pg_ctl-start.out.log'
        $ctlErr = Join-Path $projectRoot '.local/pg_ctl-start.err.log'
        $args = @('-D', ('"{0}"' -f $pgData), '-l', ('"{0}"' -f $pgLog), '-o', '"-h 127.0.0.1 -p 15432"', '-w', 'start')
        $ctl = Start-Process -FilePath "$pgBin/pg_ctl.exe" -ArgumentList $args -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput $ctlOut -RedirectStandardError $ctlErr
        if (-not $ctl.WaitForExit(30000)) { throw 'PostgreSQL start timed out; inspect pg_ctl-start logs.' }
        & "$pgBin/pg_ctl.exe" -D $pgData status *> $null
        if ($LASTEXITCODE -ne 0) { throw 'PostgreSQL start failed; inspect pg_ctl-start logs.' }
        $LASTEXITCODE = 0
    } elseif ($Action -eq 'stop') {
        & "$pgBin/pg_ctl.exe" -D $pgData -m fast -w stop
    } else {
        & "$pgBin/pg_ctl.exe" -D $pgData status
    }
    if ($LASTEXITCODE -ne 0) { throw 'Development PostgreSQL command failed.' }
} finally { $env:PATH = $originalPath }

param([ValidateSet('start','stop','status')][string]$Action = 'status')
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$layout = Get-Content -LiteralPath (Join-Path $projectRoot '.local/test-layout.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$python = Join-Path $projectRoot '.venv/Scripts/python.exe'
$stateFile = Join-Path $projectRoot '.local/test-processes.json'
$launcher = Join-Path $projectRoot 'deploy/windows/launch-service.ps1'
$shellExe = (Get-Process -Id $PID).Path
$account = [Security.Principal.WindowsIdentity]::GetCurrent().Name

function Get-OwnedProcess($entry) {
    $process = Get-Process -Id $entry.pid -ErrorAction SilentlyContinue
    if ($process -and $process.StartTime.ToUniversalTime().Ticks.ToString() -eq $entry.started) {
        $detail = Get-CimInstance Win32_Process -Filter "ProcessId=$($entry.pid)"
        if ($detail.CommandLine -and $detail.CommandLine.Contains($launcher)) { return $process }
    }
    return $null
}

$entries = @()
if (Test-Path -LiteralPath $stateFile) {
    # ConvertFrom-Json emits a JSON array as ONE object (PowerShell 5.1), so it must be assigned
    # before piping - otherwise the loop below sees a single array item and never matches a PID.
    # It also returns $null for '[]', and a $null entry breaks Get-OwnedProcess with a parameter
    # binding error that makes 'start' fail right after a 'stop' wrote '[]'.
    $parsed = Get-Content -LiteralPath $stateFile -Raw | ConvertFrom-Json
    $entries = @($parsed | Where-Object { $_ })
}
if ($Action -eq 'stop') {
    foreach ($entry in $entries) {
        if (Get-OwnedProcess $entry) {
            # Only stop the saved launcher and its current descendants, never by image name.
            & $python -c 'import psutil,sys; p=psutil.Process(int(sys.argv[1])); owned=p.children(recursive=True); p.terminate(); [q.terminate() for q in owned if q.is_running()]; _,alive=psutil.wait_procs([p,*owned],timeout=10); [q.kill() for q in alive]' $entry.pid
            if ($LASTEXITCODE -ne 0) { throw 'Could not stop an owned test process.' }
        }
    }
    '[]' | Set-Content -LiteralPath $stateFile -Encoding UTF8
    Write-Output 'Test API and worker stopped. PostgreSQL remains available for development tests.'
    exit 0
}
if ($Action -eq 'start') {
    if ($entries | Where-Object { Get-OwnedProcess $_ }) { throw 'Test processes already exist; use status or stop first.' }
    if (Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue) { throw 'Port 8765 is already occupied.' }
    $pgBin = Join-Path $projectRoot '.local/postgres/Library/bin'
    $pgData = $layout.postgres
    if ($layout.postgres_launch) { $pgData = $layout.postgres_launch }
    & "$pgBin/pg_ctl.exe" -D $pgData status *> $null
    if ($LASTEXITCODE -ne 0) { & (Join-Path $PSScriptRoot 'dev-db.ps1') start }
    $entries = @()
    foreach ($mode in @('api','worker')) {
        $arguments = @('-NoProfile','-ExecutionPolicy','Bypass','-File', ('"{0}"' -f $launcher), '-Mode', $mode, '-PythonExe', ('"{0}"' -f $python), '-ConfigFile', ('"{0}"' -f $layout.config), '-Account', ('"{0}"' -f $account))
        $process = Start-Process -FilePath $shellExe -ArgumentList $arguments -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $layout.logs "$mode.out.log") -RedirectStandardError (Join-Path $layout.logs "$mode.err.log")
        $entries += @{ mode=$mode; pid=$process.Id; started=$process.StartTime.ToUniversalTime().Ticks.ToString() }
        ConvertTo-Json -InputObject @($entries) | Set-Content -LiteralPath $stateFile -Encoding UTF8
    }
    $ready = $false
    for ($attempt=0; $attempt -lt 30; $attempt++) {
        try { $ready = (Invoke-RestMethod 'http://127.0.0.1:8765/health/ready' -TimeoutSec 2).status -eq 'ready' } catch { $ready = $false }
        if (@($entries | Where-Object { Get-OwnedProcess $_ }).Count -ne 2) { break }
        if ($ready) { break }
        Start-Sleep -Seconds 1
    }
    if (-not $ready -or @($entries | Where-Object { Get-OwnedProcess $_ }).Count -ne 2) {
        throw 'Test startup did not complete; inspect protected logs and run stop before retrying.'
    }
}
foreach ($entry in $entries) {
    [pscustomobject]@{ Mode=$entry.mode; PID=$entry.pid; Running=[bool](Get-OwnedProcess $entry) }
}
try { Invoke-RestMethod 'http://127.0.0.1:8765/health/ready' -TimeoutSec 3 } catch { Write-Output 'Test API is unavailable.' }

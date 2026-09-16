param(
    [Parameter(Mandatory)][ValidateSet('api','worker')][string]$Mode,
    [Parameter(Mandatory)][string]$PythonExe,
    [Parameter(Mandatory)][string]$ConfigFile,
    [Parameter(Mandatory)][string]$Account
)
$ErrorActionPreference = 'Stop'
try {
    . (Join-Path $PSScriptRoot 'config-loader.ps1')
    Import-ProtectedKbConfig -ConfigFile $ConfigFile -Account $Account
    if ($env:AGICO_KB_MODEL_OFFLINE -ne 'true' -or -not $env:AGICO_KB_EXPECTED_MODEL_IDENTITY) { throw 'Service requires provisioned offline model and expected identity.' }
    foreach ($PathValue in @($PythonExe, $env:AGICO_KB_STORAGE_ROOT, $env:AGICO_KB_MODEL_CACHE)) {
        if (-not $PathValue -or -not [IO.Path]::IsPathRooted($PathValue)) { throw 'Runtime paths must be absolute.' }
    }
    if (Test-Path -LiteralPath (Join-Path $env:AGICO_KB_STORAGE_ROOT 'RESTORE_INCOMPLETE')) { throw 'Restore is incomplete.' }
    if ($Mode -eq 'api') {
        & $PythonExe -m uvicorn agico_kb.main:app_factory --factory --host 127.0.0.1 --port 8765 --no-access-log
    } else {
        & $PythonExe -m agico_kb.worker
    }
    exit $LASTEXITCODE
} catch {
    # Never emit config values, exception locals, or a credential-bearing traceback.
    Write-Error 'Service launch failed. Review protected runtime configuration and ACLs.' -ErrorAction Continue
    exit 1
}

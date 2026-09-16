param(
    [Parameter(Mandatory)][ValidateSet('api','worker')][string]$Mode,
    [Parameter(Mandatory)][string]$AppRoot,
    [Parameter(Mandatory)][string]$DataRoot,
    [Parameter(Mandatory)][ValidateRange(1024,65535)][int]$ApiPort,
    [Parameter(Mandatory)][string]$Account
)
$ErrorActionPreference = 'Stop'
try {
    $ActualSid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    $ExpectedSid = ([Security.Principal.NTAccount]::new($Account)).Translate([Security.Principal.SecurityIdentifier]).Value
    if ($ActualSid -ne $ExpectedSid) { throw 'Incorrect service identity.' }
    . (Join-Path $PSScriptRoot 'config-loader.ps1')
    Import-ProtectedKbConfig -ConfigFile (Join-Path $DataRoot "config/$Mode.json") -Account $Account
    if ($env:AGICO_KB_MODEL_OFFLINE -ne 'true' -or -not $env:AGICO_KB_EXPECTED_MODEL_IDENTITY) { throw 'Offline models are required.' }
    if (Test-Path -LiteralPath (Join-Path $env:AGICO_KB_STORAGE_ROOT 'RESTORE_INCOMPLETE')) { throw 'Restore is incomplete.' }
    $env:TEMP = Join-Path $DataRoot "temp/$Mode"
    $env:TMP = $env:TEMP
    $env:PYTHONDONTWRITEBYTECODE = '1'
    $env:HF_HUB_OFFLINE = '1'
    $env:TRANSFORMERS_OFFLINE = '1'
    $env:PYTHONPATH = Join-Path $AppRoot 'src'
    Set-Location -LiteralPath $AppRoot
    $PythonExe = Join-Path $AppRoot '.runtime/venv/Scripts/python.exe'
    if ($Mode -eq 'api') {
        & $PythonExe -m uvicorn agico_kb.main:app_factory --factory --host 127.0.0.1 --port $ApiPort --no-access-log
    } else {
        & $PythonExe -m agico_kb.worker
    }
    exit $LASTEXITCODE
} catch {
    Write-Error 'Server launch failed. Review protected configuration, model identity and ACLs.' -ErrorAction Continue
    exit 1
}

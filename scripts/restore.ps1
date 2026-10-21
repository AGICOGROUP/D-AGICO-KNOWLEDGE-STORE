param(
    [Parameter(Mandatory)][string]$PythonExe,
    [Parameter(Mandatory)][string]$PgBin,
    [Parameter(Mandatory)][string]$Archive,
    [Parameter(Mandatory)][string]$NewDatabase,
    [Parameter(Mandatory)][string]$NewStorage,
    [string]$RuntimeRole
)
$ErrorActionPreference = 'Stop'
# AGICO_KB_MAINTENANCE_URL must be injected into the protected caller environment.
$Arguments = @('-m','agico_kb.operations','restore','--archive',$Archive,'--pg-bin',$PgBin,'--database',$NewDatabase,'--storage',$NewStorage)
if ($RuntimeRole) { $Arguments += @('--runtime-role',$RuntimeRole) }
& $PythonExe @Arguments
if ($LASTEXITCODE -ne 0) { throw 'Restore failed; isolated artifacts retained. Do not switch traffic.' }

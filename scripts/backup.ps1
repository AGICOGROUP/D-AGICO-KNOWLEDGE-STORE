param(
    [Parameter(Mandatory)][string]$PythonExe,
    [Parameter(Mandatory)][string]$PgBin,
    [Parameter(Mandatory)][string]$Archive
)
$ErrorActionPreference = 'Stop'
# Credentials come only from the protected caller environment, never command arguments.
& $PythonExe -m agico_kb.operations backup --archive $Archive --pg-bin $PgBin
if ($LASTEXITCODE -ne 0) { throw 'Backup failed; incomplete artifacts retained.' }

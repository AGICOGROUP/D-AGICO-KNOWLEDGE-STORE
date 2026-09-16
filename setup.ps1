[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$DataRoot,
    [ValidateRange(1024,65535)][int]$ApiPort = 8765,
    [ValidateRange(1024,65535)][int]$DatabasePort = 15432,
    [ValidatePattern('^[A-Za-z][A-Za-z0-9_]{2,40}$')][string]$ServicePrefix = 'AgicoKb'
)
$ErrorActionPreference = 'Stop'
& (Join-Path $PSScriptRoot 'scripts/bootstrap-server.ps1') @PSBoundParameters

param(
    [Parameter(Mandatory)][string]$PythonExe,
    [Parameter(Mandatory)][string]$AppRoot,
    [Parameter(Mandatory)][string]$ConfigFile,
    [Parameter(Mandatory)][string]$OutputDirectory,
    [Parameter(Mandatory)][string]$ServiceAccount,
    [string]$WinSWExe,
    [string]$WinSWSha256,
    [string]$TargetComputer,
    [PSCredential]$Credential,
    [switch]$AccountProvisioned,
    [switch]$Install
)
$ErrorActionPreference = 'Stop'
# Passwords are accepted only as PSCredential for normal accounts; gMSA is optional.
if ($ServiceAccount -notmatch '^([^\\]+)\\([^\\]+)$') { throw 'Supply a dedicated MACHINE\account or DOMAIN\account; no LocalSystem fallback.' }
$AccountDomain = $Matches[1]
$AccountUser = $Matches[2]
$IsGmsa = $AccountUser.EndsWith('$')
if ($AccountUser -in @('LocalSystem','SYSTEM','LocalService','NetworkService','Administrator')) { throw 'Use a dedicated unprivileged account.' }
foreach ($PathValue in @($PythonExe, $AppRoot, $ConfigFile, $OutputDirectory)) {
    if (-not [IO.Path]::IsPathRooted($PathValue) -or $PathValue.Contains('"')) { throw 'Absolute paths without quotes are required.' }
}
if (-not (Test-Path -LiteralPath $PythonExe -PathType Leaf)) { throw 'Python executable missing.' }
$Launcher = Join-Path $AppRoot 'deploy\windows\launch-service.ps1'
if (-not (Test-Path -LiteralPath $Launcher -PathType Leaf)) { throw 'Service launcher missing.' }
if (Test-Path -LiteralPath $OutputDirectory) { throw 'Use a new output directory to preserve existing service configuration.' }
if ($Install) {
    $Operator = [Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())
    if (-not $Operator.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'Explicit service installation requires an elevated administrator session.' }
    if ($TargetComputer -ne $env:COMPUTERNAME -or -not $AccountProvisioned) { throw 'Explicit matching TargetComputer and AccountProvisioned required.' }
    if (-not $WinSWExe -or -not [IO.Path]::IsPathRooted($WinSWExe) -or $WinSWSha256 -notmatch '^[a-fA-F0-9]{64}$') { throw 'Pinned wrapper path and independently verified SHA256 required.' }
    if ((Get-FileHash -LiteralPath $WinSWExe -Algorithm SHA256).Hash -ne $WinSWSha256) { throw 'Wrapper checksum mismatch.' }
    if ((Get-Item -LiteralPath $WinSWExe).VersionInfo.FileVersion -notmatch '^2\.12\.0(?:\.0)?$') { throw 'WinSW 2.12.0 required.' }
    if (-not $IsGmsa -and (-not $Credential -or $Credential.UserName -ne $ServiceAccount)) { throw 'Normal account installation requires a matching PSCredential, e.g. Get-Credential. Never put a password in arguments or XML.' }
    foreach ($ServiceId in @('AgicoKbApi','AgicoKbWorker')) {
        if (Get-Service -Name $ServiceId -ErrorAction SilentlyContinue) { throw 'Service already exists; review upgrades manually.' }
    }
    . (Join-Path $AppRoot 'deploy\windows\config-loader.ps1')
    Import-ProtectedKbConfig -ConfigFile $ConfigFile -Account $ServiceAccount
}
New-Item -ItemType Directory -Path $OutputDirectory | Out-Null
function EscapeXml([string]$Value) { return [Security.SecurityElement]::Escape($Value) }
$PowerShellExe = Join-Path $env:WINDIR 'System32\WindowsPowerShell\v1.0\powershell.exe'
foreach ($Mode in @('api','worker')) {
    $ServiceId = if ($Mode -eq 'api') { 'AgicoKbApi' } else { 'AgicoKbWorker' }
    $ArgumentText = '-NoLogo -NoProfile -NonInteractive -File "{0}" -Mode {1} -PythonExe "{2}" -ConfigFile "{3}" -Account "{4}"' -f $Launcher,$Mode,$PythonExe,$ConfigFile,$ServiceAccount
    $XmlText = [IO.File]::ReadAllText((Join-Path $AppRoot "deploy\windows\$Mode.xml"))
    $Replacements = @{
        '__EXECUTABLE__' = $PowerShellExe
        '__ARGUMENTS__' = $ArgumentText
        '__APP_ROOT__' = $AppRoot
        '__LOG_PATH__' = (Join-Path $OutputDirectory 'logs')
        '__ACCOUNT_DOMAIN__' = $AccountDomain
        '__ACCOUNT_USER__' = $AccountUser
    }
    foreach ($Key in $Replacements.Keys) { $XmlText = $XmlText.Replace($Key, (EscapeXml $Replacements[$Key])) }
    [xml]$null = $XmlText
    $XmlPath = Join-Path $OutputDirectory "$ServiceId.xml"
    [IO.File]::WriteAllText($XmlPath, $XmlText, [Text.UTF8Encoding]::new($false))
    if ($Install) {
        $Wrapper = Join-Path $OutputDirectory "$ServiceId.exe"
        Copy-Item -LiteralPath $WinSWExe -Destination $Wrapper
        if ($IsGmsa) {
            & $Wrapper install
            if ($LASTEXITCODE -ne 0) { throw 'Service install failed; inspect new service state. Services have not been started.' }
        } else {
            # New-Service passes credentials through SCM, not process argv or XML.
            New-Service -Name $ServiceId -DisplayName "AGICO Knowledge Store $Mode" -BinaryPathName ('"' + $Wrapper + '"') -StartupType Automatic -Credential $Credential | Out-Null
            & "$env:WINDIR\System32\sc.exe" config $ServiceId start= delayed-auto | Out-Null
            if ($LASTEXITCODE -ne 0) { throw 'Delayed automatic start configuration failed.' }
            & "$env:WINDIR\System32\sc.exe" failure $ServiceId reset= 3600 actions= restart/10000 | Out-Null
            if ($LASTEXITCODE -ne 0) { throw 'Restart policy configuration failed.' }
            # Match WinSW 2.12.0 native installation; unprivileged service cannot create this source.
            if (-not [Diagnostics.EventLog]::SourceExists($ServiceId)) {
                [Diagnostics.EventLog]::CreateEventSource($ServiceId, 'Application')
            }
        }
    }
}
Write-Output 'Service files generated. Services are not started. Review ACLs, runtime config, and readiness before starting.'

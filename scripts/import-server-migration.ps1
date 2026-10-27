#Requires -Version 5.1
[CmdletBinding()]
param(
    [string]$Archive='D:\AGICO-KB-data-migration-20261026.zip',
    [string]$AppRoot='D:\AGICO-KNOWLEDGE-STORE',
    [string]$DataRoot='D:\AGICO-KB-DATA',
    [string]$ExpectedSha256='REPLACE_WITH_VERIFIED_ARCHIVE_SHA256'
)
$ErrorActionPreference='Stop'
Set-StrictMode -Version 2
function Assert-PlainPath([string]$Path) {
    $item=$Path
    while($item) {
        if((Test-Path -LiteralPath $item) -and ((Get-Item -LiteralPath $item -Force).Attributes -band [IO.FileAttributes]::ReparsePoint)) {throw 'Migration paths cannot traverse reparse points.'}
        $item=Split-Path -Path $item -Parent
    }
}
function Protect-Private([string]$Path) {
    Assert-PlainPath $Path
    $item=Get-Item -LiteralPath $Path -Force
    if($item.PSIsContainer){$acl=New-Object Security.AccessControl.DirectorySecurity} else {$acl=New-Object Security.AccessControl.FileSecurity}
    $acl.SetAccessRuleProtection($true,$false)
    foreach($sid in @('S-1-5-18','S-1-5-32-544')) {
        $inherit=[Security.AccessControl.InheritanceFlags]::None
        if($item.PSIsContainer){$inherit=[Security.AccessControl.InheritanceFlags]'ContainerInherit,ObjectInherit'}
        $rule=New-Object Security.AccessControl.FileSystemAccessRule((New-Object Security.Principal.SecurityIdentifier($sid)),[Security.AccessControl.FileSystemRights]::FullControl,$inherit,[Security.AccessControl.PropagationFlags]::None,[Security.AccessControl.AccessControlType]::Allow)
        $acl.AddAccessRule($rule)
    }
    Set-Acl -LiteralPath $Path -AclObject $acl
}
$principal=New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if(-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)){throw 'Administrator PowerShell is required.'}
foreach($path in @($Archive,$AppRoot,$DataRoot)) {
    if($path -notmatch '^[A-Za-z]:[\\/]' -or $path -match '["\r\n]'){throw 'Expected local absolute paths.'}
    Assert-PlainPath $path
}
$AppRoot=[IO.Path]::GetFullPath($AppRoot).TrimEnd('\');$DataRoot=[IO.Path]::GetFullPath($DataRoot).TrimEnd('\')
$marker=Get-Content -LiteralPath (Join-Path $DataRoot 'config/deployment.json') -Raw -Encoding UTF8|ConvertFrom-Json
if($marker.app_root -ne $AppRoot -or $marker.data_root -ne $DataRoot -or $marker.stage -ne 'installed'){throw 'This is not the expected installed company instance.'}
if($marker.service_prefix -notmatch '^[A-Za-z][A-Za-z0-9_]{2,40}$'){throw 'Invalid service prefix.'}
$https=Get-Content -LiteralPath (Join-Path $DataRoot 'config/https/deployment.json') -Raw -Encoding UTF8|ConvertFrom-Json
if($https.mode -ne 'maintenance'){throw 'HTTPS must remain in maintenance mode during import.'}
Import-Module (Join-Path $PSHOME 'Modules/Microsoft.PowerShell.Utility/Microsoft.PowerShell.Utility.psd1') -ErrorAction Stop
if($ExpectedSha256 -notmatch '^[a-fA-F0-9]{64}$'){throw 'Expected archive checksum was not supplied.'}
if((Get-FileHash -LiteralPath $Archive -Algorithm SHA256).Hash -ne $ExpectedSha256){throw 'Migration archive checksum mismatch; no data changed.'}
Protect-Private $Archive
$stagingRoot=Join-Path $DataRoot 'config/migrations'
Assert-PlainPath $stagingRoot
if(-not(Test-Path -LiteralPath $stagingRoot)){$null=New-Item -ItemType Directory -Path $stagingRoot;Protect-Private $stagingRoot}
$stage=Join-Path $stagingRoot $ExpectedSha256.Substring(0,16).ToLowerInvariant()
Assert-PlainPath $stage
$receipt=Join-Path $stage '.archive.sha256'
if(Test-Path -LiteralPath $stage) {
    if(-not(Test-Path -LiteralPath $receipt) -or (Get-Content -LiteralPath $receipt -Raw).Trim() -ne $ExpectedSha256){throw 'Incomplete or unowned migration staging directory; preserved for inspection.'}
} else {
    $null=New-Item -ItemType Directory -Path $stage;Protect-Private $stage
    Expand-Archive -LiteralPath $Archive -DestinationPath $stage
    [IO.File]::WriteAllText($receipt,$ExpectedSha256,(New-Object Text.UTF8Encoding($false)))
}
$bundle=Join-Path $stage 'bundle'
$helper=Join-Path $bundle 'import-company-data.py'
$python=Join-Path $AppRoot '.runtime/venv/Scripts/python.exe'
foreach($path in @($bundle,$helper,$python)){Assert-PlainPath $path}
if(-not(Test-Path -LiteralPath $helper -PathType Leaf)){throw 'Verified import helper missing.'}
& $python -X utf8 $helper --app-root $AppRoot --data-root $DataRoot --bundle $bundle --check-only
if($LASTEXITCODE -ne 0){throw 'Migration preflight failed; existing services and data preserved.'}
foreach($role in @('Api','Worker')) {
    $name=$marker.service_prefix+$role
    $service=Get-CimInstance Win32_Service -Filter "Name='$name'"
    $wrapper=Join-Path $DataRoot "services/$name.exe"
    if(-not $service -or ($service.PathName.Trim() -ne ('"'+$wrapper+'"') -and $service.PathName.Trim() -ne $wrapper) -or $service.StartName -ne "NT SERVICE\$name"){throw 'Service ownership mismatch; stopping nothing else.'}
}
foreach($role in @('Worker','Api')){Stop-Service -Name ($marker.service_prefix+$role)}
& $python -X utf8 $helper --app-root $AppRoot --data-root $DataRoot --bundle $bundle
if($LASTEXITCODE -ne 0){throw 'Migration did not complete. Data retained; API and worker remain stopped; HTTPS remains in maintenance mode.'}
# Reuse the installed service ownership/ACL/configuration flow; database and TLS are preserved.
& (Join-Path $AppRoot 'scripts/configure-server-services.ps1') -AppRoot $AppRoot -DataRoot $DataRoot -Action Install
$response=Invoke-RestMethod -Uri ("http://127.0.0.1:"+$marker.api_port+'/v1/auth/settings') -TimeoutSec 10
if($response.auth_mode -ne 'dingtalk') {
    foreach($role in @('Worker','Api')){Stop-Service -Name ($marker.service_prefix+$role)}
    throw 'DingTalk mode did not activate; backend stopped; HTTPS maintenance page retained.'
}
Write-Output 'IMPORT_COMPLETE: original DingTalk bindings, roles, originals and provider settings restored.'
Write-Output 'LOCAL_AUTH_MODE: dingtalk. PUBLIC_HTTPS_MODE: maintenance.'
Get-Service -Name ($marker.service_prefix+'Database'),($marker.service_prefix+'Api'),($marker.service_prefix+'Worker'),($marker.service_prefix+'Https') | Select-Object Name,Status

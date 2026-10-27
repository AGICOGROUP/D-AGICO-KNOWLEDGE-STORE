#Requires -Version 5.1
[CmdletBinding()]
param([string]$AppRoot='D:\AGICO-KNOWLEDGE-STORE',[string]$DataRoot='D:\AGICO-KB-DATA',[switch]$ClearDocuments)
$ErrorActionPreference='Stop'
Set-StrictMode -Version 2
$PayloadBase64='RELEASE_PAYLOAD_BASE64'
$PayloadHash='RELEASE_PAYLOAD_SHA256'
$AllowedFiles=@('src/agico_kb/accounts.py','src/agico_kb/dingtalk_identity.py','src/agico_kb/db.py','src/agico_kb/operations.py','src/agico_kb/library_maintenance.py','src/agico_kb/migrations/008_access_extensions.sql','src/agico_kb/portal/admin.js','src/agico_kb/portal/account.js','scripts/company-library-maintenance.py','scripts/configure-server-services.ps1')
function Plain([string]$Path) {
    $walk=[IO.Path]::GetFullPath($Path)
    while($walk) {
        if((Test-Path -LiteralPath $walk) -and ((Get-Item -LiteralPath $walk -Force).Attributes -band [IO.FileAttributes]::ReparsePoint)){throw 'Reparse paths are not supported.'}
        $walk=Split-Path -Path $walk -Parent
    }
}
function Child([string]$Root,[string]$Name) {
    if($Name -match '(^/|\\|:|(^|/)\.\.(/|$))'){throw 'Unsafe package path.'}
    $target=[IO.Path]::GetFullPath((Join-Path $Root $Name))
    if(-not $target.StartsWith($Root.TrimEnd('\')+'\',[StringComparison]::OrdinalIgnoreCase)){throw 'Package path escapes root.'}
    Plain $target
    return $target
}
function Protect([string]$Path) {
    $acl=New-Object Security.AccessControl.DirectorySecurity
    $acl.SetAccessRuleProtection($true,$false)
    foreach($id in @('S-1-5-18','S-1-5-32-544')) {
        $sid=New-Object Security.Principal.SecurityIdentifier($id)
        $rule=New-Object Security.AccessControl.FileSystemAccessRule($sid,'FullControl','ContainerInherit,ObjectInherit','None','Allow')
        $acl.AddAccessRule($rule)
    }
    Set-Acl -LiteralPath $Path -AclObject $acl
}
function Files([string]$Root) {
    Plain $Root
    foreach($item in Get-ChildItem -LiteralPath $Root -Force) {
        Plain $item.FullName
        if($item.PSIsContainer){Files $item.FullName}else{$item}
    }
}
function Hash([byte[]]$Bytes) {
    $sha=[Security.Cryptography.SHA256]::Create()
    try{return ([BitConverter]::ToString($sha.ComputeHash($Bytes))).Replace('-','').ToLowerInvariant()}finally{$sha.Dispose()}
}
function Helper([string]$Action,[switch]$Clear) {
    $arguments=@('-X','utf8',$script:HelperPath,$Action,'--app-root',$AppRoot,'--data-root',$DataRoot,'--archive',$script:Backup)
    if($Clear){$arguments+='--clear-documents'}
    & $script:Python @arguments
    if($LASTEXITCODE -ne 0){throw ('Maintenance helper failed during '+$Action+'. See '+$script:Backup+'\maintenance-error.txt')}
}
function Owned([string]$Role) {
    $name=$script:Marker.service_prefix+$Role
    $value=Get-CimInstance Win32_Service -Filter "Name='$name'"
    $wrapper=Join-Path $DataRoot ('services/'+$name+'.exe')
    Plain $wrapper
    if(-not $value -or $value.StartName -ne ('NT SERVICE\'+$name) -or ($value.PathName.Trim() -ne ('"'+$wrapper+'"') -and $value.PathName.Trim() -ne $wrapper)){throw ('Service ownership mismatch: '+$Role)}
    return $name
}

$admin=New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if(-not $admin.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)){throw 'Administrator PowerShell required.'}
$os=Get-CimInstance Win32_OperatingSystem
if($os.Version -notlike '6.3.*' -or $os.ProductType -eq 1){throw 'Run this script on the company Windows Server 2012 R2, not on the local Windows 10 PC.'}
foreach($root in @($AppRoot,$DataRoot)) {
    if($root -notmatch '^[A-Za-z]:[\\/]' -or $root -match '["\r\n]'){throw 'Absolute local deployment paths are required.'}
    Plain $root
}
$AppRoot=[IO.Path]::GetFullPath($AppRoot).TrimEnd('\')
$DataRoot=[IO.Path]::GetFullPath($DataRoot).TrimEnd('\')
$script:Marker=Get-Content -LiteralPath (Join-Path $DataRoot 'config/deployment.json') -Raw -Encoding UTF8 | ConvertFrom-Json
if($Marker.app_root -ne $AppRoot -or $Marker.data_root -ne $DataRoot -or $Marker.stage -ne 'installed' -or $Marker.service_prefix -notmatch '^[A-Za-z][A-Za-z0-9_]{2,40}$'){throw 'Deployment identity mismatch.'}
$script:Python=Join-Path $AppRoot '.runtime/venv/Scripts/python.exe'
Plain $Python
if(-not (Test-Path -LiteralPath $Python -PathType Leaf)){throw 'Deployed Python runtime missing.'}
$api=Owned 'Api';$worker=Owned 'Worker';$database=Owned 'Database'
if((Get-Service $database).Status -ne 'Running'){throw 'Database service must be healthy before maintenance.'}
$archiveRoot=Join-Path $DataRoot 'maintenance'
Plain $archiveRoot
if(-not (Test-Path -LiteralPath $archiveRoot)){New-Item -ItemType Directory -Path $archiveRoot | Out-Null}
Protect $archiveRoot
$script:Backup=Join-Path $archiveRoot ('20261027-library-'+(Get-Date -Format 'yyyyMMdd-HHmmss')+'-'+[guid]::NewGuid().ToString('N').Substring(0,8))
New-Item -ItemType Directory -Path $Backup | Out-Null
Protect $Backup
$stage=Join-Path $Backup 'package'
New-Item -ItemType Directory -Path $stage | Out-Null
$bytes=[Convert]::FromBase64String($PayloadBase64)
if((Hash $bytes) -ne $PayloadHash){throw 'Embedded update payload checksum failed.'}
Add-Type -AssemblyName System.IO.Compression
$stream=New-Object IO.MemoryStream(,$bytes)
$zip=New-Object IO.Compression.ZipArchive($stream,[IO.Compression.ZipArchiveMode]::Read)
try {
    $seen=@{}
    foreach($entry in $zip.Entries) {
        $name=$entry.FullName
        if($name -notin ($AllowedFiles+@('manifest.json')) -or $seen.ContainsKey($name)){throw 'Unexpected package entry.'}
        $seen[$name]=$true
        $target=Child $stage $name
        [IO.Directory]::CreateDirectory((Split-Path $target -Parent)) | Out-Null
        $inputStream=$entry.Open();$outputStream=[IO.File]::Create($target)
        try{$inputStream.CopyTo($outputStream)}finally{$inputStream.Dispose();$outputStream.Dispose()}
    }
    if($seen.Count -ne ($AllowedFiles.Count+1)){throw 'Update package is incomplete.'}
} finally {$zip.Dispose();$stream.Dispose()}
$manifest=Get-Content -LiteralPath (Join-Path $stage 'manifest.json') -Raw -Encoding UTF8 | ConvertFrom-Json
foreach($name in $AllowedFiles) {
    if((Hash ([IO.File]::ReadAllBytes((Child $stage $name)))) -ne $manifest.files.$name){throw 'Source payload checksum failed.'}
}
$script:HelperPath=Join-Path $stage 'scripts/company-library-maintenance.py'
$env:PYTHONPATH=Join-Path $AppRoot 'src'
$env:PYTHONDONTWRITEBYTECODE='1'
Write-Output 'COMPANY_LIBRARY_UPDATE: 20261027-ACCESS1'
Helper 'preflight'
Write-Output ('BACKUP_DIRECTORY: '+$Backup)
$stopped=$false
try {
    foreach($name in @($worker,$api)) {
        $control=Get-Service $name
        if($control.Status -ne 'Stopped'){$control.Stop()}
        $control.WaitForStatus([ServiceProcess.ServiceControllerStatus]::Stopped,[TimeSpan]::FromSeconds(90))
        $control.Dispose()
    }
    $stopped=$true
    # Preserve configuration and source bytes before replacing code. No secrets are printed.
    $configRoot=Join-Path $DataRoot 'config'
    $configBackup=Join-Path $Backup 'configuration'
    foreach($item in Files $configRoot) {
        Plain $item.FullName
        $relative=$item.FullName.Substring($configRoot.Length+1).Replace('\','/')
        $target=Child $configBackup $relative
        [IO.Directory]::CreateDirectory((Split-Path $target -Parent)) | Out-Null
        [IO.File]::Copy($item.FullName,$target,$false)
        if((Hash ([IO.File]::ReadAllBytes($target))) -ne (Hash ([IO.File]::ReadAllBytes($item.FullName)))){throw 'Configuration backup checksum failed.'}
    }
    foreach($name in $AllowedFiles) {
        $target=Child $AppRoot $name
        if(Test-Path -LiteralPath $target) {
            $old=Child (Join-Path $Backup 'source-before') $name
            [IO.Directory]::CreateDirectory((Split-Path $old -Parent)) | Out-Null
            [IO.File]::Copy($target,$old,$false)
        }
        # Replacing file bytes preserves its existing ACL; new files inherit the protected source directory ACL.
        [IO.File]::WriteAllBytes($target,[IO.File]::ReadAllBytes((Child $stage $name)))
        foreach($service in @($api,$worker)) {
            & "$env:SystemRoot/System32/icacls.exe" $target /grant ('NT SERVICE\'+$service+':(RX)') /Q | Out-Null
            if($LASTEXITCODE -ne 0){throw 'Could not grant service access to patched source.'}
        }
        if((Hash ([IO.File]::ReadAllBytes($target))) -ne $manifest.files.$name){throw 'Installed source checksum failed.'}
    }
    Helper 'apply' -Clear:$ClearDocuments
    $retired=Join-Path $Backup 'library-clear/retired-originals'
    if(Test-Path -LiteralPath $retired) {
        $null=@(Files $retired)
        Protect $retired
        foreach($child in Get-ChildItem -LiteralPath $retired -Force) {
            & "$env:SystemRoot/System32/icacls.exe" $child.FullName /reset /T /Q /L | Out-Null
            if($LASTEXITCODE -ne 0){throw 'Could not restrict retired originals to administrators.'}
        }
    }
    foreach($item in Files $configBackup) {
        $relative=$item.FullName.Substring($configBackup.Length+1).Replace('\','/')
        $current=Child $configRoot $relative
        if((Hash ([IO.File]::ReadAllBytes($current))) -ne (Hash ([IO.File]::ReadAllBytes($item.FullName)))){throw 'Protected configuration changed.'}
    }
    foreach($name in @($api,$worker)) {
        $control=Get-Service $name;$control.Start()
        $control.WaitForStatus([ServiceProcess.ServiceControllerStatus]::Running,[TimeSpan]::FromSeconds(45));$control.Dispose()
    }
    $ready=$false
    for($attempt=0;$attempt -lt 30;$attempt++) {
        try {
            $health=Invoke-WebRequest -UseBasicParsing -Uri ('http://127.0.0.1:'+$Marker.api_port+'/health/ready') -TimeoutSec 3
            if(($health.Content|ConvertFrom-Json).status -eq 'ready'){$ready=$true;break}
        }catch{}
        Start-Sleep -Seconds 1
    }
    if(-not $ready){throw 'Backend readiness timed out; backup and maintenance result are retained.'}
    Helper 'verify'
    $stopped=$false
    Write-Output ('BACKUP_DIRECTORY: '+$Backup)
    Get-Service $api,$worker,$database | Format-Table Name,Status -AutoSize
} catch {
    if($stopped){Write-Warning 'Update verification failed. Do not rerun old deployment/import scripts. Preserve the backup directory printed above.'}
    throw
}

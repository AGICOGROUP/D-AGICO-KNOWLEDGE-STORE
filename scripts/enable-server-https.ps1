#Requires -Version 5.1
[CmdletBinding()]
param(
    [string]$AppRoot='D:\AGICO-KNOWLEDGE-STORE',
    [string]$DataRoot='D:\AGICO-KB-DATA'
)
$ErrorActionPreference='Stop'
Set-StrictMode -Version 2
# The public delivery generator embeds the verified Python helper here.
$HelperBase64='RELEASE_HELPER_BASE64'
function Assert-PlainPath([string]$Path) {
    $current=$Path
    while($current) {
        if((Test-Path -LiteralPath $current) -and ((Get-Item -LiteralPath $current -Force).Attributes -band [IO.FileAttributes]::ReparsePoint)) {throw 'Reparse paths refused.'}
        $current=Split-Path -Path $current -Parent
    }
}
function Invoke-Helper([string]$Action,[string]$Mode='proxy',[string]$Output='') {
    $values=@('-X','utf8',$script:Helper,$Action,'--data-root',$DataRoot,'--hostname',$script:Hostname,'--port',[string]$script:Port,'--backend-port',[string]$script:ApiPort,'--mode',$Mode)
    if($Output){$values+=@('--output',$Output)}
    & $script:Python @values
    if($LASTEXITCODE -ne 0){throw ('HTTPS check failed: '+$Action)}
}
function Test-Nginx([string]$Configuration) {
    & $script:Nginx -p $script:Prefix -c $Configuration.Replace('\','/') -e $script:ErrorLog -t
    if($LASTEXITCODE -ne 0){throw 'NGINX configuration validation failed.'}
}
function Grant-ConfigurationRead([string]$Path) {
    Assert-PlainPath $Path
    $acl=Get-Acl -LiteralPath $Path
    $sid=New-Object Security.Principal.SecurityIdentifier($script:HttpsSid)
    $rule=New-Object Security.AccessControl.FileSystemAccessRule($sid,[Security.AccessControl.FileSystemRights]::Read,[Security.AccessControl.AccessControlType]::Allow)
    $acl.AddAccessRule($rule)
    Set-Acl -LiteralPath $Path -AclObject $acl
}
function Clear-StuckOwnedIngress {
    $current=Get-CimInstance Win32_Service -Filter "Name='$name'"
    if(-not $current -or $current.State -ne 'Stop Pending' -or $current.ProcessId -eq 0 -or $current.StartName -ne "NT SERVICE\$name" -or ($current.PathName.Trim() -ne ('"'+$wrapper+'"') -and $current.PathName.Trim() -ne $wrapper)) {
        throw 'Refusing process recovery: owned service identity or stop-pending state changed.'
    }
    $parent=Get-CimInstance Win32_Process -Filter ("ProcessId="+$current.ProcessId)
    if(-not $parent -or $parent.ExecutablePath -ne $wrapper){throw 'Service wrapper process identity changed.'}
    $masters=@(Get-CimInstance Win32_Process -Filter ("ParentProcessId="+$current.ProcessId) | Where-Object {$_.ExecutablePath -eq $script:Nginx})
    if($masters.Count -eq 0){throw 'No verified owned NGINX child exists; automatic force recovery refused.'}
    foreach($master in $masters) {
        $fresh=Get-CimInstance Win32_Process -Filter ("ProcessId="+$master.ProcessId)
        if(-not $fresh){continue}
        if($fresh.ExecutablePath -ne $script:Nginx -or $fresh.ParentProcessId -ne $current.ProcessId -or $fresh.CreationDate -ne $master.CreationDate){throw 'NGINX process identity changed.'}
        # OS console helpers and any non-NGINX child are left untouched.
        $children=@(Get-CimInstance Win32_Process -Filter ("ParentProcessId="+$master.ProcessId) | Where-Object {$_.ExecutablePath -eq $script:Nginx})
        # Only the verified web proxy master and its verified NGINX children.
        # No name-wide process kills and no database or document-storage access.
        Write-Output ('RECOVERING_OWNED_HTTPS_PROCESS: '+$master.ProcessId)
        Stop-Process -Id $master.ProcessId -Force -ErrorAction Stop
        foreach($child in $children) {
            $live=Get-CimInstance Win32_Process -Filter ("ProcessId="+$child.ProcessId)
            if($live -and $live.ExecutablePath -eq $script:Nginx -and $live.ParentProcessId -eq $master.ProcessId -and $live.CreationDate -eq $child.CreationDate) {
                Stop-Process -Id $child.ProcessId -Force -ErrorAction Stop
            }
        }
    }
    $controller=Get-Service -Name $name
    $controller.WaitForStatus([ServiceProcess.ServiceControllerStatus]::Stopped,[TimeSpan]::FromSeconds(25))
}
function Stop-OwnedIngress {
    # NGINX control events belong to its virtual service account. An
    # administrator cannot necessarily signal them; use SCM instead.
    $controller=Get-Service -Name $name -ErrorAction Stop
    if($controller.Status -eq [ServiceProcess.ServiceControllerStatus]::StopPending) {
        Clear-StuckOwnedIngress
    } elseif($controller.Status -ne [ServiceProcess.ServiceControllerStatus]::Stopped) {
        $controller.Stop()
        try {$controller.WaitForStatus([ServiceProcess.ServiceControllerStatus]::Stopped,[TimeSpan]::FromSeconds(35))}
        catch [System.ServiceProcess.TimeoutException] {Clear-StuckOwnedIngress}
    }
}
function Restart-OwnedIngress {
    Stop-OwnedIngress
    $controller=Get-Service -Name $name -ErrorAction Stop
    $controller.Start()
    $controller.WaitForStatus([ServiceProcess.ServiceControllerStatus]::Running,[TimeSpan]::FromSeconds(30))
}
function Wait-Mode([string]$Mode) {
    for($attempt=0;$attempt -lt 12;$attempt++) {
        & $script:Python -X utf8 $script:Helper probe --data-root $DataRoot --hostname $script:Hostname --port $script:Port --mode $Mode 2>$null | Out-Null
        if($LASTEXITCODE -eq 0){return}
        Start-Sleep -Milliseconds 500
    }
    throw ('HTTPS did not enter '+$Mode+' mode.')
}
$principal=New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if(-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)){throw 'Administrator PowerShell required.'}
Write-Output 'ACTIVATION_SCRIPT: 20261027-ACL3'
foreach($root in @($AppRoot,$DataRoot)) {
    if($root -notmatch '^[A-Za-z]:[\\/]' -or $root -match '["\r\n]'){throw 'Expected local absolute paths.'}
    Assert-PlainPath $root
}
$AppRoot=[IO.Path]::GetFullPath($AppRoot).TrimEnd('\')
$DataRoot=[IO.Path]::GetFullPath($DataRoot).TrimEnd('\')
$marker=Get-Content -LiteralPath (Join-Path $DataRoot 'config/deployment.json') -Raw -Encoding UTF8 | ConvertFrom-Json
if($marker.app_root -ne $AppRoot -or $marker.data_root -ne $DataRoot -or $marker.stage -ne 'installed' -or $marker.service_prefix -notmatch '^[A-Za-z][A-Za-z0-9_]{2,40}$'){throw 'Installed deployment identity did not match.'}
$statePath=Join-Path $DataRoot 'config/https/deployment.json'
$state=Get-Content -LiteralPath $statePath -Raw -Encoding UTF8 | ConvertFrom-Json
$script:Hostname='zsk.agicogroup.com';$script:Port=8888;$script:ApiPort=[int]$marker.api_port
if($script:ApiPort -lt 1024 -or $script:ApiPort -gt 65535 -or $script:ApiPort -eq $script:Port){throw 'Invalid loopback API port.'}
if($state.app_root -ne $AppRoot -or $state.data_root -ne $DataRoot -or $state.hostname -ne $script:Hostname -or $state.port -ne $script:Port -or $state.mode -notin @('maintenance','proxy')){throw 'HTTPS installation identity did not match.'}
$runtime=Get-Content -LiteralPath (Join-Path $DataRoot 'config/runtime.json') -Raw -Encoding UTF8 | ConvertFrom-Json
if($runtime.AGICO_KB_PUBLIC_URL -ne 'https://zsk.agicogroup.com:8888' -or $runtime.AGICO_KB_DINGTALK_CONFIG_FILE -ne (Join-Path $DataRoot 'config/dingtalk.json')){throw 'Expected migrated DingTalk runtime was not found.'}
$name=$marker.service_prefix+'Https'
$wrapper=Join-Path $DataRoot "services/$name.exe"
$service=Get-CimInstance Win32_Service -Filter "Name='$name'"
if(-not $service -or $service.StartName -ne "NT SERVICE\$name" -or ($service.PathName.Trim() -ne ('"'+$wrapper+'"') -and $service.PathName.Trim() -ne $wrapper) -or $service.State -notin @('Running','Stopped','Stop Pending')){throw 'Owned HTTPS service identity or state did not match.'}
$script:HttpsSid=([Security.Principal.NTAccount]::new("NT SERVICE\$name")).Translate([Security.Principal.SecurityIdentifier]).Value
$confRoot=Join-Path $DataRoot 'config/https'
$script:Conf=Join-Path $confRoot 'nginx.conf'
$script:Helper=Join-Path $AppRoot 'scripts/https-ingress.py'
$script:Python=Join-Path $AppRoot '.runtime/venv/Scripts/python.exe'
$script:Nginx=Join-Path $AppRoot '.runtime/nginx/nginx.exe'
$script:Prefix=(Join-Path $DataRoot 'logs/https').Replace('\','/')+'/'
$script:ErrorLog=(Join-Path $DataRoot 'logs/https/error.log').Replace('\','/')
foreach($path in @($confRoot,$script:Conf,$statePath,$script:Helper,$script:Python,$script:Nginx,(Join-Path $DataRoot 'config/tls/fullchain.pem'),(Join-Path $DataRoot 'config/tls/private.key'))) {Assert-PlainPath $path}
Import-Module (Join-Path $PSHOME 'Modules/Microsoft.PowerShell.Utility/Microsoft.PowerShell.Utility.psd1') -ErrorAction Stop
$lock=Get-Content -LiteralPath (Join-Path $AppRoot 'deploy/windows/https-lock.json') -Raw -Encoding UTF8 | ConvertFrom-Json
if((Get-FileHash -LiteralPath $script:Nginx -Algorithm SHA256).Hash -ne $lock.exe_sha256){throw 'NGINX checksum mismatch.'}
$backup=Join-Path $confRoot ('before-proxy-'+(Get-Date -Format 'yyyyMMdd-HHmmss')+'-'+[Guid]::NewGuid().ToString('N').Substring(0,8))
$null=New-Item -ItemType Directory -Path $backup
Copy-Item -LiteralPath $script:Conf -Destination (Join-Path $backup 'nginx.conf')
Copy-Item -LiteralPath $statePath -Destination (Join-Path $backup 'deployment.json')
Copy-Item -LiteralPath $script:Helper -Destination (Join-Path $backup 'https-ingress.py')
(Get-Acl -LiteralPath $script:Conf).Sddl | Set-Content -LiteralPath (Join-Path $backup 'nginx-before.sddl') -Encoding ASCII
if($HelperBase64 -ne 'RELEASE_HELPER_BASE64') {
    # Overwrite the owned helper only; no executable or credentials are replaced.
    [IO.File]::WriteAllBytes($script:Helper,[Convert]::FromBase64String($HelperBase64))
}
Invoke-Helper 'verify-backend'
Grant-ConfigurationRead $script:Conf
Grant-ConfigurationRead $statePath
if($state.mode -eq 'proxy') {
    if($service.State -ne 'Running'){Restart-OwnedIngress;Wait-Mode 'proxy'}
    Invoke-Helper 'verify-application'
    Write-Output 'HTTPS_APPLICATION_READY: existing proxy and DingTalk authentication verified.'
    exit
}
$candidate=Join-Path $confRoot ('nginx-proxy-'+[Guid]::NewGuid().ToString('N')+'.conf')
$originalConfig=[IO.File]::ReadAllBytes($script:Conf)
$originalState=[IO.File]::ReadAllBytes($statePath)
$changed=$false
try {
    Invoke-Helper 'render' 'proxy' $candidate
    Test-Nginx $candidate
    # Atomic replacement. Config contains certificate paths, never private key bytes.
    [IO.File]::Replace($candidate,$script:Conf,(Join-Path $backup 'replace-original.conf'))
    $changed=$true
    Grant-ConfigurationRead $script:Conf
    Restart-OwnedIngress
    Wait-Mode 'proxy'
    Invoke-Helper 'verify-application'
    $state.mode='proxy'
    [IO.File]::WriteAllText($statePath,($state | ConvertTo-Json),(New-Object Text.UTF8Encoding($false)))
} catch {
    $failure=$_.Exception.GetBaseException()
    Write-Output ('HTTPS_ERROR: '+$failure.GetType().Name+': '+$failure.Message)
    if($changed) {
        [IO.File]::WriteAllBytes($script:Conf,$originalConfig)
        [IO.File]::WriteAllBytes($statePath,$originalState)
        try {
            Grant-ConfigurationRead $script:Conf
            Test-Nginx $script:Conf
            Restart-OwnedIngress
            Wait-Mode 'maintenance'
            Write-Output 'ROLLBACK_VERIFIED: HTTPS maintenance mode restored.'
        } catch {
            # If maintenance cannot be verified, close only this owned public ingress.
            try {
                Stop-OwnedIngress
                Write-Output 'HTTPS_STOPPED: owned HTTPS service is stopped; backend data and services were preserved.'
            } catch {
                Write-Output 'HTTPS_RECOVERY_BLOCKED: could not verify shutdown; existing processes were preserved for inspection.'
            }
        }
    }
    throw 'HTTPS activation failed. Review the checks above; do not rerun the migration.'
}
Write-Output 'HTTPS_APPLICATION_READY: https://zsk.agicogroup.com:8888/'
Write-Output 'AUTH_MODE: dingtalk. ANONYMOUS_DOCUMENTS: denied (401).'
Write-Output ('CONFIG_BACKUP: '+$backup)
Get-Service -Name $name | Select-Object Name,Status

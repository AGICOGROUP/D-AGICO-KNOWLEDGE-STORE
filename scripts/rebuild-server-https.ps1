#Requires -Version 5.1
[CmdletBinding()]
param([string]$AppRoot='D:\AGICO-KNOWLEDGE-STORE',[string]$DataRoot='D:\AGICO-KB-DATA')
$ErrorActionPreference='Stop'
Set-StrictMode -Version 2
$HelperBase64='RELEASE_HELPER_BASE64'
function Plain([string]$Path) {
    $p=$Path
    while($p) {
        if((Test-Path -LiteralPath $p) -and ((Get-Item -LiteralPath $p -Force).Attributes -band [IO.FileAttributes]::ReparsePoint)){throw 'Reparse path refused.'}
        $p=Split-Path -Path $p -Parent
    }
}
function Protect([string]$Path,[string]$Rights) {
    Plain $Path
    $item=Get-Item -LiteralPath $Path -Force
    if($item.PSIsContainer){$acl=New-Object Security.AccessControl.DirectorySecurity}else{$acl=New-Object Security.AccessControl.FileSecurity}
    $acl.SetAccessRuleProtection($true,$false)
    $grants=@{'S-1-5-18'='FullControl';'S-1-5-32-544'='FullControl'}
    if($Rights){$grants[$script:Sid]=$Rights}
    foreach($id in $grants.Keys) {
        $inherit=[Security.AccessControl.InheritanceFlags]::None
        if($item.PSIsContainer){$inherit=[Security.AccessControl.InheritanceFlags]'ContainerInherit,ObjectInherit'}
        $rule=New-Object Security.AccessControl.FileSystemAccessRule((New-Object Security.Principal.SecurityIdentifier($id)),[Security.AccessControl.FileSystemRights]$grants[$id],$inherit,[Security.AccessControl.PropagationFlags]::None,[Security.AccessControl.AccessControlType]::Allow)
        $acl.AddAccessRule($rule)
    }
    Set-Acl -LiteralPath $Path -AclObject $acl
}
function Traverse([string]$Path) {
    Plain $Path
    $acl=Get-Acl -LiteralPath $Path
    $rule=New-Object Security.AccessControl.FileSystemAccessRule((New-Object Security.Principal.SecurityIdentifier($script:Sid)),'ReadAndExecute','Allow')
    $acl.SetAccessRule($rule)
    Set-Acl -LiteralPath $Path -AclObject $acl
}
function Invoke-OwnedSc([string[]]$Values) {
    $quoted=foreach($value in $Values){'"'+(($value -replace '(\\*)"','$1$1\"') -replace '(\\+)$','$1$1')+'"'}
    $info=New-Object Diagnostics.ProcessStartInfo
    $info.FileName="$env:SystemRoot\System32\sc.exe";$info.Arguments=$quoted -join ' '
    $info.UseShellExecute=$false;$info.CreateNoWindow=$true;$info.RedirectStandardOutput=$true;$info.RedirectStandardError=$true
    $p=[Diagnostics.Process]::Start($info)
    $null=$p.StandardOutput.ReadToEnd();$null=$p.StandardError.ReadToEnd();$p.WaitForExit()
    $code=$p.ExitCode;$p.Dispose()
    if($code -ne 0){throw ('Service control failed: '+$Values[0]+' (exit '+$code+')')}
}
function Check([string]$Action,[string]$Mode='proxy') {
    & $script:Python -X utf8 $script:Helper $Action --data-root $DataRoot --hostname zsk.agicogroup.com --port 8888 --backend-port $script:ApiPort --mode $Mode
    if($LASTEXITCODE -ne 0){throw ('Check failed: '+$Action)}
}
function Syntax {
    & $script:Nginx -p $script:Prefix -c $script:Conf.Replace('\','/') -e $script:ErrorLog -t
    if($LASTEXITCODE -ne 0){throw 'NGINX syntax or certificate validation failed.'}
}
function Stop-Ingress {
    $c=Get-Service -Name $script:Name -ErrorAction SilentlyContinue
    if($c) {
        if($c.Status -eq 'Running'){$c.Stop()}
        $c.WaitForStatus([ServiceProcess.ServiceControllerStatus]::Stopped,[TimeSpan]::FromSeconds(30))
        $c.Dispose()
    }
}
function Start-Ingress([string]$Mode='proxy') {
    $c=Get-Service -Name $script:Name
    $c.Start()
    $c.WaitForStatus([ServiceProcess.ServiceControllerStatus]::Running,[TimeSpan]::FromSeconds(60))
    $c.Dispose()
    for($attempt=0;$attempt -lt 12;$attempt++) {
        & $script:Python -X utf8 $script:Helper probe --data-root $DataRoot --hostname zsk.agicogroup.com --port 8888 --mode $Mode | Out-Null
        if($LASTEXITCODE -eq 0){return}
        Start-Sleep -Milliseconds 500
    }
    throw 'Service did not pass certificate and HTTPS verification.'
}
$admin=New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if(-not $admin.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)){throw 'Administrator PowerShell required.'}
Write-Output 'HTTPS_REBUILD: 20261027-CLEAN1'
foreach($root in @($AppRoot,$DataRoot)) {
    if($root -notmatch '^[A-Za-z]:[\\/]' -or $root -match '["\r\n]'){throw 'Expected absolute local paths.'}
    Plain $root
}
$AppRoot=[IO.Path]::GetFullPath($AppRoot).TrimEnd('\')
$DataRoot=[IO.Path]::GetFullPath($DataRoot).TrimEnd('\')
$marker=Get-Content -LiteralPath (Join-Path $DataRoot 'config/deployment.json') -Raw -Encoding UTF8 | ConvertFrom-Json
if($marker.app_root -ne $AppRoot -or $marker.data_root -ne $DataRoot -or $marker.stage -ne 'installed' -or $marker.service_prefix -notmatch '^[A-Za-z][A-Za-z0-9_]{2,40}$'){throw 'Deployment identity mismatch.'}
$script:Name=$marker.service_prefix+'Https'
$wrapper=Join-Path $DataRoot "services/$script:Name.exe"
$xmlPath=Join-Path $DataRoot "services/$script:Name.xml"
$script:ApiPort=[int]$marker.api_port
if($script:ApiPort -lt 1024 -or $script:ApiPort -gt 65535 -or $script:ApiPort -eq 8888){throw 'Invalid backend port.'}
$existing=Get-CimInstance Win32_Service -Filter "Name='$script:Name'"
if($existing -and ($existing.StartName -ne "NT SERVICE\$script:Name" -or ($existing.PathName.Trim() -ne ('"'+$wrapper+'"') -and $existing.PathName.Trim() -ne $wrapper))){throw 'HTTPS service ownership mismatch.'}
if($existing -and $existing.State -ne 'Stopped'){throw ('HTTPS service must be stopped before rebuilding; current state: '+$existing.State)}
if(Get-NetTCPConnection -State Listen -LocalPort 8888 -ErrorAction SilentlyContinue){throw 'Port 8888 is occupied; no existing listener was stopped.'}
$confRoot=Join-Path $DataRoot 'config/https'
$statePath=Join-Path $confRoot 'deployment.json'
$state=Get-Content -LiteralPath $statePath -Raw -Encoding UTF8 | ConvertFrom-Json
if($state.app_root -ne $AppRoot -or $state.data_root -ne $DataRoot -or $state.hostname -ne 'zsk.agicogroup.com' -or $state.port -ne 8888){throw 'Existing HTTPS identity mismatch.'}
$runtime=Get-Content -LiteralPath (Join-Path $DataRoot 'config/runtime.json') -Raw -Encoding UTF8 | ConvertFrom-Json
if($runtime.AGICO_KB_PUBLIC_URL -ne 'https://zsk.agicogroup.com:8888' -or $runtime.AGICO_KB_DINGTALK_CONFIG_FILE -ne (Join-Path $DataRoot 'config/dingtalk.json')){throw 'Migrated DingTalk runtime is required.'}
$script:Conf=Join-Path $confRoot 'nginx.conf'
$script:Helper=Join-Path $AppRoot 'scripts/https-ingress.py'
$script:Python=Join-Path $AppRoot '.runtime/venv/Scripts/python.exe'
$nginxRoot=Join-Path $AppRoot '.runtime/nginx'
$script:Nginx=Join-Path $nginxRoot 'nginx.exe'
$winsw=Join-Path $AppRoot '.runtime/winsw.exe'
$logRoot=Join-Path $DataRoot 'logs/https'
$script:Prefix=$logRoot.Replace('\','/')+'/'
$script:ErrorLog=(Join-Path $logRoot 'error.log').Replace('\','/')
$tlsRoot=Join-Path $DataRoot 'config/tls'
foreach($path in @($wrapper,$xmlPath,$confRoot,$statePath,$script:Conf,$script:Helper,$script:Python,$script:Nginx,$winsw,$logRoot,$tlsRoot)){Plain $path}
Import-Module (Join-Path $PSHOME 'Modules/Microsoft.PowerShell.Utility/Microsoft.PowerShell.Utility.psd1') -ErrorAction Stop
Import-Module (Join-Path $PSHOME 'Modules/Microsoft.PowerShell.Security/Microsoft.PowerShell.Security.psd1') -ErrorAction Stop
$lock=Get-Content -LiteralPath (Join-Path $AppRoot 'deploy/windows/https-lock.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$boot=Get-Content -LiteralPath (Join-Path $AppRoot 'deploy/windows/bootstrap-lock.json') -Raw -Encoding UTF8 | ConvertFrom-Json
if((Get-FileHash -LiteralPath $script:Nginx -Algorithm SHA256).Hash -ne $lock.exe_sha256 -or (Get-FileHash -LiteralPath $winsw -Algorithm SHA256).Hash -ne $boot.artifacts.winsw.sha256){throw 'Owned program checksum mismatch.'}
$sha=[Security.Cryptography.SHA1]::Create()
try {$hash=$sha.ComputeHash([Text.Encoding]::Unicode.GetBytes($script:Name.ToUpperInvariant()))}finally{$sha.Dispose()}
$parts=for($i=0;$i -lt 20;$i+=4){[BitConverter]::ToUInt32($hash,$i).ToString()}
$script:Sid='S-1-5-80-'+($parts -join '-')
$backupRoot=Join-Path $DataRoot 'config/https-rebuild-backups'
Plain $backupRoot
if(-not(Test-Path -LiteralPath $backupRoot)){$null=New-Item -ItemType Directory -Path $backupRoot;Protect $backupRoot ''}
$backup=Join-Path $backupRoot ((Get-Date -Format 'yyyyMMdd-HHmmss')+'-'+[Guid]::NewGuid().ToString('N').Substring(0,8))
$null=New-Item -ItemType Directory -Path $backup
if($existing){$existing | Select-Object Name,PathName,StartName,StartMode,State | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $backup 'service-before.json') -Encoding UTF8}
foreach($path in @($script:Conf,$statePath,$xmlPath,$script:Helper)) {
    if(Test-Path -LiteralPath $path -PathType Leaf) {
        Copy-Item -LiteralPath $path -Destination (Join-Path $backup (Split-Path $path -Leaf))
        (Get-Acl -LiteralPath $path).Sddl | Set-Content -LiteralPath (Join-Path $backup ((Split-Path $path -Leaf)+'.sddl')) -Encoding ASCII
    }
}
if($HelperBase64 -ne 'RELEASE_HELPER_BASE64'){[IO.File]::WriteAllBytes($script:Helper,[Convert]::FromBase64String($HelperBase64))}
Write-Output 'STEP 1/5: verifying DingTalk backend and denied anonymous document access.'
Check 'verify-backend'
$changed=$false
try {
    Write-Output 'STEP 2/5: recreating only the owned HTTPS service.'
    if($existing) {
        Invoke-OwnedSc @('delete',$script:Name)
        for($i=0;$i -lt 20;$i++) {
            if(-not(Get-CimInstance Win32_Service -Filter "Name='$script:Name'")){break}
            Start-Sleep -Milliseconds 500
        }
        if(Get-CimInstance Win32_Service -Filter "Name='$script:Name'"){throw 'Old HTTPS service is still marked for deletion; preserved all files.'}
    }
    $changed=$true
    $eventKey='HKLM:\SYSTEM\CurrentControlSet\Services\EventLog\Application\'+$script:Name
    if(-not(Test-Path -LiteralPath $eventKey)){New-EventLog -LogName Application -Source $script:Name}
    foreach($parent in @($AppRoot,(Join-Path $AppRoot '.runtime'),$DataRoot,(Join-Path $DataRoot 'config'),(Join-Path $DataRoot 'services'),(Join-Path $DataRoot 'logs'))){Traverse $parent}
    Protect $confRoot 'ReadAndExecute'
    Protect $logRoot 'Modify'
    foreach($sub in @('body','proxy','fastcgi','uwsgi','scgi')) {
        $p=Join-Path $logRoot $sub;Plain $p
        $null=New-Item -ItemType Directory -Path $p -Force
        Protect $p 'Modify'
    }
    Protect $nginxRoot 'ReadAndExecute';Protect $script:Nginx 'ReadAndExecute'
    Protect $tlsRoot 'ReadAndExecute'
    foreach($file in @('fullchain.pem','private.key')){Protect (Join-Path $tlsRoot $file) 'Read'}
    Write-Output 'STEP 3/5: generating configuration and explicit service permissions.'
    Check 'render'
    Protect $script:Conf 'Read'
    $state.mode='maintenance'
    [IO.File]::WriteAllText($statePath,($state|ConvertTo-Json),(New-Object Text.UTF8Encoding($false)))
    Protect $statePath 'Read'
    Syntax
    Copy-Item -LiteralPath $winsw -Destination $wrapper -Force
    Protect $wrapper 'ReadAndExecute'
    $escape={param($value) [Security.SecurityElement]::Escape([string]$value)}
    $exeXml=& $escape $script:Nginx;$logXml=& $escape $logRoot;$dirXml=& $escape $nginxRoot
    $argsXml=& $escape ('-p "'+$script:Prefix+'" -c "'+$script:Conf.Replace('\','/')+'" -e "'+$script:ErrorLog+'"')
    # Use WinSW's bounded default stop. Do not depend on NGINX control events
    # or launch a second stop executable that can wait indefinitely.
    $xml=@"
<service><id>$script:Name</id><name>AGICO Knowledge HTTPS</name><description>Owned AGICO HTTPS ingress.</description>
<executable>$exeXml</executable><arguments>$argsXml</arguments><workingdirectory>$dirXml</workingdirectory>
<logpath>$logXml</logpath><log mode="roll-by-size"><sizeThreshold>10240</sizeThreshold><keepFiles>5</keepFiles></log>
<startmode>Automatic</startmode><stoptimeout>10sec</stoptimeout><stopparentprocessfirst>true</stopparentprocessfirst>
</service>
"@
    [IO.File]::WriteAllText($xmlPath,$xml,(New-Object Text.UTF8Encoding($false)))
    Protect $xmlPath 'Read'
    Invoke-OwnedSc @('create',$script:Name,'binPath=',('"'+$wrapper+'"'),'start=','auto','obj=',"NT SERVICE\$script:Name",'DisplayName=','AGICO Knowledge HTTPS')
    Write-Output 'STEP 4/5: starting HTTPS and checking certificate and application.'
    Start-Ingress
    Check 'verify-application'
    Write-Output 'STEP 5/5: verifying a complete stop and restart.'
    Stop-Ingress
    Start-Ingress
    Check 'verify-application'
    Invoke-OwnedSc @('failure',$script:Name,'reset=','3600','actions=','restart/10000/restart/10000/restart/10000')
    $ruleName=$script:Name+'-8888'
    $rule=Get-NetFirewallRule -Name $ruleName -ErrorAction SilentlyContinue
    if($rule) {
        if($rule.Group -ne 'AGICO Knowledge HTTPS'){throw 'Existing firewall rule is not owned by this deployment.'}
    } else {
        New-NetFirewallRule -Name $ruleName -DisplayName 'AGICO Knowledge HTTPS 8888' -Group 'AGICO Knowledge HTTPS' -Direction Inbound -Action Allow -Protocol TCP -LocalPort 8888 -Program $script:Nginx -Profile Any | Out-Null
    }
    $state.mode='proxy'
    [IO.File]::WriteAllText($statePath,($state|ConvertTo-Json),(New-Object Text.UTF8Encoding($false)))
    Protect $statePath 'Read'
} catch {
    $failure=$_.Exception.GetBaseException()
    Write-Output ('HTTPS_REBUILD_ERROR: '+$failure.GetType().Name+': '+$failure.Message)
    if($changed -and (Get-CimInstance Win32_Service -Filter "Name='$script:Name'")) {
        try {
            Stop-Ingress
            Check 'render' 'maintenance'
            Protect $script:Conf 'Read'
            $state.mode='maintenance'
            [IO.File]::WriteAllText($statePath,($state|ConvertTo-Json),(New-Object Text.UTF8Encoding($false)))
            Protect $statePath 'Read'
            Syntax
            Start-Ingress 'maintenance'
            Write-Output 'ROLLBACK_VERIFIED: clean HTTPS maintenance listener restored.'
        } catch {
            try {Stop-Ingress;Write-Output 'HTTPS_STOPPED: owned HTTPS service stopped.'}catch{Write-Output 'HTTPS_STATE_NEEDS_INSPECTION'}
        }
    }
    Write-Output ('BACKUP: '+$backup)
    Get-CimInstance Win32_Service -Filter "Name='$script:Name'" | Select-Object Name,State,ProcessId
    throw 'HTTPS rebuild did not complete. Database, identities, provider keys and documents were not modified.'
}
Write-Output 'HTTPS_APPLICATION_READY: https://zsk.agicogroup.com:8888/'
Write-Output 'STOP_RESTART_VERIFIED. AUTH_MODE: dingtalk. ANONYMOUS_DOCUMENTS: denied (401).'
Write-Output ('BACKUP: '+$backup)
Get-Service -Name $script:Name | Select-Object Name,Status

#Requires -Version 5.1
[CmdletBinding()]
param(
    [string]$AppRoot='D:\AGICO-KNOWLEDGE-STORE',
    [string]$DataRoot='D:\AGICO-KB-DATA',
    [string]$Hostname='zsk.agicogroup.com',
    [ValidateRange(1024,65535)][int]$Port=8888,
    [ValidateSet('Install','Status','Stop')][string]$Action='Install'
)
$ErrorActionPreference='Stop'
Set-StrictMode -Version 2
function Assert-PlainPath([string]$Path) {
    $p=$Path
    while($p) {
        if((Test-Path -LiteralPath $p) -and ((Get-Item -LiteralPath $p -Force).Attributes -band [IO.FileAttributes]::ReparsePoint)) { throw 'Reparse paths refused.' }
        $p=Split-Path -Path $p -Parent
    }
}
function Protect-Path([string]$Path,[string]$Rights='ReadAndExecute') {
    Assert-PlainPath $Path
    $item=Get-Item -LiteralPath $Path -Force
    if($item.PSIsContainer) {$acl=New-Object Security.AccessControl.DirectorySecurity} else {$acl=New-Object Security.AccessControl.FileSecurity}
    $acl.SetAccessRuleProtection($true,$false)
    $grants=@{'S-1-5-18'='FullControl';'S-1-5-32-544'='FullControl'}
    $grants[$script:ServiceSid]=$Rights
    foreach($sid in $grants.Keys) {
        $inherit=[Security.AccessControl.InheritanceFlags]::None
        if($item.PSIsContainer) {$inherit=[Security.AccessControl.InheritanceFlags]'ContainerInherit,ObjectInherit'}
        $rule=New-Object Security.AccessControl.FileSystemAccessRule((New-Object Security.Principal.SecurityIdentifier($sid)),[Security.AccessControl.FileSystemRights]$grants[$sid],$inherit,[Security.AccessControl.PropagationFlags]::None,[Security.AccessControl.AccessControlType]::Allow)
        $acl.AddAccessRule($rule)
    }
    Set-Acl -LiteralPath $Path -AclObject $acl
}
function Grant-Traverse([string]$Path) {
    Assert-PlainPath $Path
    $acl=Get-Acl -LiteralPath $Path
    $acl.SetAccessRule((New-Object Security.AccessControl.FileSystemAccessRule((New-Object Security.Principal.SecurityIdentifier($script:ServiceSid)),'ReadAndExecute','Allow')))
    Set-Acl -LiteralPath $Path -AclObject $acl
}
function Invoke-ServiceControl([string[]]$Values) {
    $quoted=foreach($value in $Values) {'"'+(($value -replace '(\\*)"','$1$1\"') -replace '(\\+)$','$1$1')+'"'}
    $info=New-Object Diagnostics.ProcessStartInfo
    $info.FileName="$env:SystemRoot\System32\sc.exe";$info.Arguments=$quoted -join ' '
    $info.UseShellExecute=$false;$info.CreateNoWindow=$true;$info.RedirectStandardOutput=$true;$info.RedirectStandardError=$true
    $process=[Diagnostics.Process]::Start($info)
    $null=$process.StandardOutput.ReadToEnd();$null=$process.StandardError.ReadToEnd();$process.WaitForExit()
    $exitCode=$process.ExitCode;$process.Dispose()
    if($exitCode -ne 0) {throw 'HTTPS service registration failed.'}
}
$principal=New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if(-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {throw 'Administrator PowerShell required.'}
foreach($root in @($AppRoot,$DataRoot)) {if($root -notmatch '^[A-Za-z]:[\\/]' -or $root -match '["\r\n]') {throw 'Expected absolute paths.'}; Assert-PlainPath $root}
$AppRoot=[IO.Path]::GetFullPath($AppRoot).TrimEnd('\');$DataRoot=[IO.Path]::GetFullPath($DataRoot).TrimEnd('\')
$marker=Get-Content -LiteralPath (Join-Path $DataRoot 'config/deployment.json') -Raw -Encoding UTF8 | ConvertFrom-Json
if($marker.app_root -ne $AppRoot -or $marker.data_root -ne $DataRoot -or $marker.stage -ne 'installed') {throw 'Installed deployment identity did not match.'}
if($Hostname -notmatch '^[a-z0-9]+([.-][a-z0-9]+)*$') {throw 'Invalid hostname.'}
if($marker.service_prefix -notmatch '^[A-Za-z][A-Za-z0-9_]{2,40}$') {throw 'Invalid deployment service prefix.'}
$Name=$marker.service_prefix+'Https'
$wrapper=Join-Path $DataRoot "services/$Name.exe"
$xmlPath=Join-Path $DataRoot "services/$Name.xml"
$existing=Get-CimInstance Win32_Service -Filter "Name='$Name'"
if($existing -and (($existing.PathName.Trim() -ne ('"'+$wrapper+'"') -and $existing.PathName.Trim() -ne $wrapper) -or $existing.StartName -ne "NT SERVICE\$Name")) {throw 'Existing HTTPS service is not owned by this deployment.'}
$confRoot=Join-Path $DataRoot 'config/https'
$statePath=Join-Path $confRoot 'deployment.json'
if($Action -eq 'Status') {Get-Service -Name $Name -ErrorAction Stop | Select-Object Name,Status;exit}
if($Action -eq 'Stop') {if($existing){Stop-Service -Name $Name};exit}
if(Test-Path -LiteralPath $statePath) {
    $state=Get-Content -LiteralPath $statePath -Raw -Encoding UTF8 | ConvertFrom-Json
    if($state.app_root -ne $AppRoot -or $state.data_root -ne $DataRoot -or $state.hostname -ne $Hostname -or $state.port -ne $Port) {throw 'Existing HTTPS configuration differs; no changes made.'}
    if($existing) {Write-Output 'Existing HTTPS installation preserved.';Get-Service -Name $Name | Select-Object Name,Status;exit}
} elseif($existing -or (Test-Path -LiteralPath $confRoot)) {throw 'Unowned HTTPS configuration exists.'}
if(Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue) {throw 'HTTPS port is already occupied.'}
$nginxRoot=Join-Path $AppRoot '.runtime/nginx'
$nginx=Join-Path $nginxRoot 'nginx.exe'
$python=Join-Path $AppRoot '.runtime/venv/Scripts/python.exe'
$helper=Join-Path $AppRoot 'scripts/https-ingress.py'
$cert=Join-Path $DataRoot 'config/tls/fullchain.pem'
$key=Join-Path $DataRoot 'config/tls/private.key'
foreach($file in @($nginx,$python,$helper,$cert,$key,$wrapper,$xmlPath,$confRoot,(Join-Path $DataRoot 'logs/https'))) {Assert-PlainPath $file}
foreach($file in @($nginx,$python,$helper,$cert,$key)) {if(-not(Test-Path -LiteralPath $file -PathType Leaf)) {throw 'Required HTTPS program or certificate is missing.'}}
Import-Module (Join-Path $PSHOME 'Modules/Microsoft.PowerShell.Utility/Microsoft.PowerShell.Utility.psd1') -ErrorAction Stop
$lock=Get-Content -LiteralPath (Join-Path $AppRoot 'deploy/windows/https-lock.json') -Raw -Encoding UTF8 | ConvertFrom-Json
if((Get-FileHash -LiteralPath $nginx -Algorithm SHA256).Hash -ne $lock.exe_sha256) {throw 'NGINX binary checksum mismatch.'}
$bootstrap=Get-Content -LiteralPath (Join-Path $AppRoot 'deploy/windows/bootstrap-lock.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$winsw=Join-Path $AppRoot '.runtime/winsw.exe'
if((Get-FileHash -LiteralPath $winsw -Algorithm SHA256).Hash -ne $bootstrap.artifacts.winsw.sha256) {throw 'Service wrapper checksum mismatch.'}
& $nginx -v
if($LASTEXITCODE -ne 0) {throw 'NGINX cannot run on this server.'}
$sha=[Security.Cryptography.SHA1]::Create()
try {$hash=$sha.ComputeHash([Text.Encoding]::Unicode.GetBytes($Name.ToUpperInvariant()))} finally {$sha.Dispose()}
$parts=for($i=0;$i -lt 20;$i+=4){[BitConverter]::ToUInt32($hash,$i).ToString()}
$script:ServiceSid='S-1-5-80-'+($parts -join '-')
$logRoot=Join-Path $DataRoot 'logs/https'
foreach($folder in @($confRoot,$logRoot)) {$null=New-Item -ItemType Directory -Path $folder -Force;Protect-Path $folder $(if($folder -eq $logRoot){'Modify'}else{'ReadAndExecute'})}
foreach($sub in @('body','proxy','fastcgi','uwsgi','scgi')) {Assert-PlainPath (Join-Path $logRoot $sub);$null=New-Item -ItemType Directory -Path (Join-Path $logRoot $sub) -Force}
foreach($parent in @($AppRoot,(Join-Path $AppRoot '.runtime'),$DataRoot,(Join-Path $DataRoot 'config'),(Join-Path $DataRoot 'services'),(Join-Path $DataRoot 'logs'))) {Grant-Traverse $parent}
Protect-Path $nginxRoot
foreach($file in Get-ChildItem -LiteralPath $nginxRoot -File) {Protect-Path $file.FullName}
Protect-Path (Join-Path $DataRoot 'config/tls')
foreach($file in @($cert,$key)) {Protect-Path $file 'Read'}
& $python -X utf8 $helper render --data-root $DataRoot --hostname $Hostname --port $Port
if($LASTEXITCODE -ne 0) {throw 'Could not generate HTTPS configuration.'}
$conf=Join-Path $confRoot 'nginx.conf'
Protect-Path $conf 'Read'
$prefix=$logRoot.Replace('\','/')+'/'
$errorLog=(Join-Path $logRoot 'error.log').Replace('\','/')
& $nginx -p $prefix -c $conf.Replace('\','/') -e $errorLog -t
if($LASTEXITCODE -ne 0) {throw 'NGINX configuration or certificate validation failed.'}
$state=@{app_root=$AppRoot;data_root=$DataRoot;hostname=$Hostname;port=$Port;mode='maintenance'}
$utf8=New-Object Text.UTF8Encoding($false)
[IO.File]::WriteAllText($statePath,($state | ConvertTo-Json),$utf8);Protect-Path $statePath 'Read'
Copy-Item -LiteralPath $winsw -Destination $wrapper -Force;Protect-Path $wrapper
$escape={param($value) [Security.SecurityElement]::Escape([string]$value)}
$exeXml=& $escape $nginx;$logXml=& $escape $logRoot;$dirXml=& $escape $nginxRoot
$argsXml=& $escape ('-p "'+$prefix+'" -c "'+$conf.Replace('\','/')+'" -e "'+$errorLog+'"')
$stopXml=& $escape ('-p "'+$prefix+'" -c "'+$conf.Replace('\','/')+'" -e "'+$errorLog+'" -s quit')
$xml=@"
<service><id>$Name</id><name>AGICO Knowledge HTTPS</name><description>Owned HTTPS ingress for the AGICO knowledge base.</description>
<executable>$exeXml</executable><startarguments>$argsXml</startarguments><stopexecutable>$exeXml</stopexecutable><stoparguments>$stopXml</stoparguments>
<workingdirectory>$dirXml</workingdirectory><logpath>$logXml</logpath><log mode="roll-by-size"><sizeThreshold>10240</sizeThreshold><keepFiles>5</keepFiles></log>
<startmode>Automatic</startmode><stoptimeout>30sec</stoptimeout><onfailure action="restart" delay="10sec"/><resetfailure>1hour</resetfailure></service>
"@
[IO.File]::WriteAllText($xmlPath,$xml,$utf8);Protect-Path $xmlPath 'Read'
Invoke-ServiceControl @('create',$Name,'binPath=',('"'+$wrapper+'"'),'start=','auto','obj=',"NT SERVICE\$Name",'DisplayName=','AGICO Knowledge HTTPS')
Start-Service -Name $Name
$ready=$false
for($i=0;$i -lt 15;$i++) {
    & $python -X utf8 $helper probe --data-root $DataRoot --hostname $Hostname --port $Port 2>$null
    if($LASTEXITCODE -eq 0){$ready=$true;break}
    Start-Sleep -Seconds 1
}
if(-not $ready){throw 'HTTPS service failed local certificate and readiness verification; review data/logs/https.'}
$ruleName=$Name+'-'+$Port
$rule=Get-NetFirewallRule -Name $ruleName -ErrorAction SilentlyContinue
if($rule) {
    if($rule.Group -ne 'AGICO Knowledge HTTPS') {throw 'Firewall rule name belongs to another application.'}
} else {
    New-NetFirewallRule -Name $ruleName -DisplayName "AGICO Knowledge HTTPS $Port" -Group 'AGICO Knowledge HTTPS' -Direction Inbound -Action Allow -Protocol TCP -LocalPort $Port -Program $nginx -Profile Any | Out-Null
}
Write-Output "HTTPS ingress installed: https://${Hostname}:$Port/"
Write-Output 'Maintenance mode active. No knowledge-base API is exposed until DingTalk configuration is ready.'
Get-Service -Name $Name | Select-Object Name,Status

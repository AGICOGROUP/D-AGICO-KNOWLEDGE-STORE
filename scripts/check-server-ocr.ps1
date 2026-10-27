#Requires -Version 5.1
[CmdletBinding()]
param([string]$AppRoot='D:\AGICO-KNOWLEDGE-STORE',[string]$DataRoot='D:\AGICO-KB-DATA')
$ErrorActionPreference='Stop'
$Payload='RELEASE_OCR_PROBE_BASE64'
$principal=New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if(-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)){throw 'Administrator Windows PowerShell required.'}
$marker=Get-Content -LiteralPath (Join-Path $DataRoot 'config/deployment.json') -Raw -Encoding UTF8 | ConvertFrom-Json
if($marker.app_root -ne $AppRoot -or $marker.data_root -ne $DataRoot -or $marker.stage -ne 'installed'){throw 'Deployment identity mismatch.'}
$name=$marker.service_prefix+'Worker'
if($name -notmatch '^[A-Za-z][A-Za-z0-9_]{2,45}$'){throw 'Invalid owned worker name.'}
$worker=Get-CimInstance Win32_Service -Filter "Name='$name'"
$expected=Join-Path $DataRoot ('services/'+$name+'.exe')
if(-not $worker -or $worker.State -ne 'Running' -or $worker.ProcessId -eq 0 -or $worker.StartName -ne ('NT SERVICE\'+$name) -or $worker.PathName.Trim('"') -ne $expected){throw 'Owned running worker identity did not match.'}
$config=Get-Content -LiteralPath (Join-Path $DataRoot 'config/worker.json') -Raw -Encoding UTF8 | ConvertFrom-Json
if([IO.Path]::GetFullPath($config.AGICO_KB_MODEL_CACHE).TrimEnd('\') -ne (Join-Path $DataRoot 'models')){throw 'Unexpected model cache path.'}
$parent=Join-Path $DataRoot 'temp/worker'
if(-not(Test-Path -LiteralPath $parent -PathType Container)){throw 'Worker temporary directory is missing.'}
$path=$parent
while($path){
 if((Test-Path -LiteralPath $path) -and ((Get-Item -LiteralPath $path -Force).Attributes -band [IO.FileAttributes]::ReparsePoint)){throw 'Reparse path refused.'}
 $path=Split-Path $path -Parent
}
$root=Join-Path $parent ('ocr-check-'+[Guid]::NewGuid().ToString('N'))
$null=New-Item -ItemType Directory -Path $root
$acl=New-Object Security.AccessControl.DirectorySecurity
$acl.SetAccessRuleProtection($true,$false)
$workerSid=([Security.Principal.NTAccount]::new('NT SERVICE\'+$name)).Translate([Security.Principal.SecurityIdentifier]).Value
foreach($id in @('S-1-5-18','S-1-5-32-544',$workerSid)){
 $right=if($id -eq $workerSid){'Modify'}else{'FullControl'}
 $acl.AddAccessRule((New-Object Security.AccessControl.FileSystemAccessRule((New-Object Security.Principal.SecurityIdentifier($id)),$right,'ContainerInherit,ObjectInherit','None','Allow')))
}
Set-Acl -LiteralPath $root -AclObject $acl
if($Payload -eq 'RELEASE_OCR_PROBE_BASE64'){throw 'Use the delivered single-file release.'}
$script=Join-Path $root 'check.py'
[IO.File]::WriteAllBytes($script,[Convert]::FromBase64String($Payload))
$python=Join-Path $AppRoot '.runtime/venv/Scripts/python.exe'
$arguments=@('-X','utf8',$script,'--pid',[string]$worker.ProcessId,'--root',$root,'--app',$AppRoot,'--models',[string]$config.AGICO_KB_MODEL_CACHE,'--service',$name)
$quoted=foreach($argument in $arguments){'"'+(($argument -replace '(\\*)"','$1$1\"') -replace '(\\+)$','$1$1')+'"'}
$out=Join-Path $root 'stdout.log';$err=Join-Path $root 'stderr.log'
$child=Start-Process -FilePath $python -ArgumentList ($quoted -join ' ') -WorkingDirectory $AppRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput $out -RedirectStandardError $err
Write-Output 'WORKER_OCR_CHECK_STARTED: temporary synthetic scan only; allow up to 180 seconds.'
if(-not $child.WaitForExit(180000)){
 Stop-Process -Id $child.Id -Force -ErrorAction SilentlyContinue
 Write-Output 'WORKER_OCR_CHECK_TIMED_OUT'
}
if(Test-Path -LiteralPath $out){Get-Content -LiteralPath $out -Encoding UTF8 | Where-Object {$_ -match '^\{"check":'}}
$trace=Join-Path $root 'diagnostic-traceback.txt'
if(Test-Path -LiteralPath $trace){Get-Content -LiteralPath $trace -Encoding UTF8 -Tail 18}
if((Test-Path -LiteralPath $err) -and -not(Test-Path -LiteralPath $trace)){
 $last=@(Get-Content -LiteralPath $err -Tail 8 -Encoding UTF8)
 if($last){$last}
}
Write-Output ('DIAGNOSTIC_FOLDER: '+$root)

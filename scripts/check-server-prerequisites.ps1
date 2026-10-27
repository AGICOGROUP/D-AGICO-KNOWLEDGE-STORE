#Requires -Version 5.1
[CmdletBinding()]
param([string]$AppRoot = 'D:\AGICO-KNOWLEDGE-STORE')
$ErrorActionPreference = 'Stop'
$checks = @()
$os = Get-CimInstance -ClassName Win32_OperatingSystem
$checks += [pscustomobject]@{Item='OS'; Result=$os.Caption; Detail=$os.Version}
$checks += [pscustomobject]@{Item='PowerShell'; Result=$PSVersionTable.PSVersion.ToString(); Detail='Required: 5.1 or newer'}
$net = Get-ItemProperty -LiteralPath 'HKLM:\SOFTWARE\Microsoft\NET Framework Setup\NDP\v4\Full' -ErrorAction SilentlyContinue
$release = if ($net -and $net.PSObject.Properties['Release']) { [int]$net.Release } else { 0 }
$checks += [pscustomobject]@{Item='.NET 4.8 baseline'; Result=$(if($release -ge 528040){'Present'}else{'Review'}); Detail=('Release='+$release)}
$patches = @(Get-HotFix -ErrorAction SilentlyContinue | Select-Object -ExpandProperty HotFixID)
foreach($id in @('KB2919442','KB2919355','KB2999226')) {
    $checks += [pscustomobject]@{Item=$id; Result=$(if($patches -contains $id){'Listed'}else{'Not listed'}); Detail='Not listed does not exclude superseding updates'}
}
foreach($name in @('msvcp140.dll','vcruntime140.dll','vcruntime140_1.dll')) {
    $path=Join-Path $AppRoot ('.runtime\'+$name)
    $origin='App-local'
    if(-not (Test-Path -LiteralPath $path -PathType Leaf)) { $path=Join-Path ([Environment]::SystemDirectory) $name; $origin='System' }
    $version=if(Test-Path -LiteralPath $path -PathType Leaf){[Diagnostics.FileVersionInfo]::GetVersionInfo($path).FileVersion}else{'Missing'}
    $checks += [pscustomobject]@{Item=$name; Result=$version; Detail=$origin}
}
$office=Join-Path $env:ProgramFiles 'LibreOffice\program\soffice.exe'
$officeFound=Test-Path -LiteralPath $office -PathType Leaf
$checks += [pscustomobject]@{Item='LibreOffice'; Result=$(if($officeFound){'Found'}else{'Not found'}); Detail='Default 64-bit path only; required for full Office preview'}
$pending=(Test-Path 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootPending') -or (Test-Path 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Auto Update\RebootRequired')
$checks += [pscustomobject]@{Item='Pending reboot'; Result=[string]$pending; Detail='Read-only check; no restart performed'}
$checks | Format-Table -AutoSize -Wrap
Write-Host 'Read-only inventory. No installation, system change, account change or original-file change performed.'

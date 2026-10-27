#Requires -Version 5.1
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$DataRoot,
    [ValidateRange(1024,65535)][int]$ApiPort = 8765,
    [ValidateRange(1024,65535)][int]$DatabasePort = 15432,
    [ValidatePattern('^[A-Za-z][A-Za-z0-9_]{2,40}$')][string]$ServicePrefix = 'AgicoKb'
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$ProgressPreference = 'SilentlyContinue'
function Invoke-Checked([string]$Executable, [string[]]$Arguments) {
    # Windows PowerShell converts redirected native stderr (including uv progress)
    # into ErrorRecord objects. Judge native tools by their exit status instead.
    $previousPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        & $Executable @Arguments 2>&1 | ForEach-Object { Write-Host $_.ToString() }
        $nativeExit = $LASTEXITCODE
    } finally { $ErrorActionPreference = $previousPreference }
    if ($nativeExit -ne 0) { throw "Dependency command failed: $Executable (exit $nativeExit)" }
}
function Assert-MicromambaRuntime([string]$Executable) {
    # New MSVC mutex initialization is incompatible with pre-14.40 runtimes.
    # Prefer application-local DLLs; never replace the server's global runtime.
    foreach ($name in @('msvcp140.dll','vcruntime140.dll','vcruntime140_1.dll')) {
        $dll = Join-Path (Split-Path -Parent $Executable) $name
        if (-not (Test-Path -LiteralPath $dll -PathType Leaf)) {
            $dll = Join-Path ([Environment]::SystemDirectory) $name
        }
        if (-not (Test-Path -LiteralPath $dll -PathType Leaf)) {
            throw "C++ runtime missing: $name. Extract the verified app-local runtime bundle into AppRoot before retrying."
        }
        $version = [Diagnostics.FileVersionInfo]::GetVersionInfo($dll)
        if ($version.FileMajorPart -lt 14 -or ($version.FileMajorPart -eq 14 -and $version.FileMinorPart -lt 40)) {
            throw "C++ runtime is too old for micromamba: $name ($($version.FileVersion)). Extract the verified app-local runtime bundle into AppRoot; do not replace System32 DLLs."
        }
    }
}
function Install-PythonCppRuntime([string]$RuntimeRoot) {
    Import-Module (Join-Path $PSHOME 'Modules/Microsoft.PowerShell.Utility/Microsoft.PowerShell.Utility.psd1') -ErrorAction Stop
    $names = @('msvcp140.dll','vcruntime140.dll','vcruntime140_1.dll')
    $sources = @($names | Where-Object { Test-Path -LiteralPath (Join-Path $RuntimeRoot $_) -PathType Leaf })
    if ($sources.Count -eq 0) { return } # A suitable system runtime remains valid.
    if ($sources.Count -ne $names.Count) { throw 'Application-local C++ runtime bundle is incomplete.' }
    $RuntimeRoot = [IO.Path]::GetFullPath($RuntimeRoot).TrimEnd('\')
    Assert-NoReparse $RuntimeRoot
    $python = Join-Path $RuntimeRoot 'venv/Scripts/python.exe'
    Assert-NoReparse $python
    $base = & $python -I -c 'import sys; print(sys._base_executable)'
    if ($LASTEXITCODE -ne 0 -or -not $base -or @($base).Count -ne 1) { throw 'Cannot identify the owned base Python interpreter.' }
    $base = [IO.Path]::GetFullPath([string]$base)
    if (-not $base.StartsWith($RuntimeRoot + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Refusing to change Python outside this installation.'
    }
    Assert-NoReparse $base
    $directories = @((Split-Path -Parent $python), (Split-Path -Parent $base)) | Select-Object -Unique
    foreach ($directory in $directories) {
        Assert-NoReparse $directory
        foreach ($name in $names) {
            $source = Join-Path $RuntimeRoot $name
            $destination = Join-Path $directory $name
            Assert-NoReparse $source
            Assert-NoReparse $destination
            $digest = (Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash
            if ((Test-Path -LiteralPath $destination -PathType Leaf) -and
                (Get-FileHash -LiteralPath $destination -Algorithm SHA256).Hash -eq $digest) { continue }
            $temporary = Join-Path $directory ('.cpp-runtime-' + [Guid]::NewGuid().ToString('N') + '.tmp')
            try {
                Copy-Item -LiteralPath $source -Destination $temporary
                if ((Get-FileHash -LiteralPath $temporary -Algorithm SHA256).Hash -ne $digest) { throw 'Python C++ runtime copy failed verification.' }
                # Replace the directory entry, without modifying any possible hard-link source.
                Move-Item -LiteralPath $temporary -Destination $destination -Force
            } finally {
                if (Test-Path -LiteralPath $temporary -PathType Leaf) { Remove-Item -LiteralPath $temporary -Force }
            }
        }
    }
    Write-Host 'Application-local C++ runtime prepared for owned base Python and virtual environment.'
}
function Assert-NoReparse([string]$Path) {
    $cursor = $Path
    while ($cursor) {
        if (Test-Path -LiteralPath $cursor) {
            if ((Get-Item -LiteralPath $cursor -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) {
                throw "Unexpected reparse point: $cursor"
            }
        }
        $parent = Split-Path -Parent $cursor
        if ($parent -eq $cursor) { break }; $cursor = $parent
    }
}
function Write-Json([string]$Path, $Value) {
    $json = $Value | ConvertTo-Json -Depth 8
    [IO.File]::WriteAllText($Path, $json, (New-Object Text.UTF8Encoding($false)))
}
function Protect-Directory([string]$Path) {
    # PowerShell 7 can pass an incompatible PSModulePath to Windows PowerShell 5.1.
    Import-Module (Join-Path $PSHOME 'Modules/Microsoft.PowerShell.Security/Microsoft.PowerShell.Security.psd1') -ErrorAction Stop
    $acl = New-Object Security.AccessControl.DirectorySecurity
    $acl.SetAccessRuleProtection($true, $false)
    foreach ($sid in @([Security.Principal.WindowsIdentity]::GetCurrent().User.Value, 'S-1-5-18', 'S-1-5-32-544')) {
        $identity = New-Object Security.Principal.SecurityIdentifier($sid)
        $rule = New-Object Security.AccessControl.FileSystemAccessRule($identity, 'FullControl', 'ContainerInherit,ObjectInherit', 'None', 'Allow')
        $acl.AddAccessRule($rule)
    }
    Set-Acl -LiteralPath $Path -AclObject $acl
}
function Get-Verified($Artifact, [string]$Destination) {
    Assert-NoReparse $Destination
    Assert-NoReparse "$Destination.partial"
    if ($Artifact.url -notmatch '^https://' -or $Artifact.sha256 -notmatch '^[a-f0-9]{64}$') { throw 'Invalid dependency lock' }
    if (Test-Path -LiteralPath $Destination) {
        if ((Get-FileHash -LiteralPath $Destination -Algorithm SHA256).Hash -eq $Artifact.sha256) { return }
    }
    $partial = "$Destination.partial"
    Invoke-WebRequest -UseBasicParsing -Uri $Artifact.url -OutFile $partial
    if ((Get-FileHash -LiteralPath $partial -Algorithm SHA256).Hash -ne $Artifact.sha256) { throw "SHA256 mismatch for $($Artifact.url)" }
    Move-Item -LiteralPath $partial -Destination $Destination -Force
}
$AppRoot = [IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot)).TrimEnd('\')
if ($AppRoot -match '[^\x20-\x7E]|["'']') { throw 'AppRoot must be ASCII without quotes' }
if ($DataRoot -notmatch '^[A-Za-z]:[\\/]' -or $DataRoot -match '["'']') { throw 'DataRoot must be a local absolute directory without quotes' }
$DataRoot = [IO.Path]::GetFullPath($DataRoot).TrimEnd('\')
if ($DataRoot -eq [IO.Path]::GetPathRoot($DataRoot).TrimEnd('\')) { throw 'DataRoot cannot be a drive root' }
if ($DataRoot -eq $AppRoot -or $DataRoot.StartsWith($AppRoot + '\', [StringComparison]::OrdinalIgnoreCase) -or $AppRoot.StartsWith($DataRoot + '\', [StringComparison]::OrdinalIgnoreCase)) { throw 'AppRoot and DataRoot cannot overlap' }
if ($ApiPort -eq $DatabasePort) { throw 'API and database ports must differ' }
$setupMutexes = New-Object 'Collections.Generic.List[Threading.Mutex]'
try {
foreach ($root in @($AppRoot, $DataRoot)) {
    $hasher = [Security.Cryptography.SHA256]::Create()
    try { $digest = [BitConverter]::ToString($hasher.ComputeHash([Text.Encoding]::UTF8.GetBytes($root.ToLowerInvariant()))).Replace('-', '').ToLowerInvariant() }
    finally { $hasher.Dispose() }
    $mutex = New-Object Threading.Mutex($false, "Global\AgicoKbSetup_$digest")
    $acquired = $false
    try {
        try { $acquired = $mutex.WaitOne(0) }
        catch [Threading.AbandonedMutexException] { $acquired = $true }
        if (-not $acquired) { throw "Another setup is already running for $root" }
        $setupMutexes.Add($mutex)
    } catch {
        if (-not $acquired) { $mutex.Dispose() }
        throw
    }
}
Assert-NoReparse $AppRoot
Assert-NoReparse $DataRoot
if (-not [Environment]::Is64BitOperatingSystem -or -not [Environment]::Is64BitProcess -or $env:PROCESSOR_ARCHITECTURE -ne 'AMD64') { throw 'Windows x64 PowerShell is required' }
$Runtime = Join-Path $AppRoot '.runtime'
Assert-NoReparse $Runtime
$markerPath = Join-Path $DataRoot 'config/deployment.json'
$targetPath = Join-Path $Runtime 'setup-target.json'
$link = Join-Path $Runtime 'data'
if (Test-Path -LiteralPath $link) {
    $item = Get-Item -LiteralPath $link -Force
    if (-not ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -or [IO.Path]::GetFullPath([string]$item.Target[0]).TrimEnd('\') -ne $DataRoot) { throw 'Managed data junction points elsewhere' }
}
$target = [ordered]@{schema_version=1;app_root=$AppRoot;data_root=$DataRoot;api_port=$ApiPort;database_port=$DatabasePort;service_prefix=$ServicePrefix;stage='preparing'}
foreach ($path in @($markerPath, $targetPath)) {
    Assert-NoReparse $path
    if (Test-Path -LiteralPath $path) {
        $existing = Get-Content -LiteralPath $path -Raw -Encoding UTF8 | ConvertFrom-Json
        foreach ($key in @('schema_version','app_root','data_root','api_port','database_port','service_prefix')) {
            if ($existing.$key -ne $target[$key]) { throw "Deployment target mismatch: $key" }
        }
    }
}
if ((Test-Path -LiteralPath $Runtime) -and -not (Test-Path -LiteralPath $targetPath) -and @(Get-ChildItem -LiteralPath $Runtime -Force).Count) { throw 'Refusing unowned nonempty .runtime' }
if ((Test-Path -LiteralPath $DataRoot) -and -not (Test-Path -LiteralPath $markerPath)) {
    if (@(Get-ChildItem -LiteralPath $DataRoot -Force | Where-Object Name -ne 'desktop.ini').Count) { throw 'Refusing nonempty unowned DataRoot' }
}
$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'Run setup from an administrator PowerShell' }
$PrivateProviders = Join-Path $AppRoot '.deployment-private'
if (Test-Path -LiteralPath $PrivateProviders) {
    Assert-NoReparse $PrivateProviders
    Protect-Directory $PrivateProviders
}
if (Test-Path -LiteralPath $markerPath) {
    $marker = Get-Content -LiteralPath $markerPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($marker.stage -notin @('preparing','initialized','installed')) { throw 'Unknown deployment stage' }
    if ($marker.stage -eq 'installed') {
        if (-not (Test-Path -LiteralPath $targetPath)) { throw 'Installed deployment is missing setup-target.json' }
        & (Join-Path $PSScriptRoot 'configure-server-services.ps1') -AppRoot $AppRoot -DataRoot $DataRoot -Action Status
        & (Join-Path $PSScriptRoot 'configure-server-services.ps1') -AppRoot $AppRoot -DataRoot $DataRoot -Action Start
        return
    }
    if ($marker.stage -eq 'initialized') {
        if (-not (Test-Path -LiteralPath $targetPath)) { throw 'Initialized deployment is missing setup-target.json' }
        foreach ($relative in @('venv/Scripts/python.exe','postgres/Library/bin/postgres.exe','postgres/Library/bin/pg_ctl.exe','winsw.exe')) {
            $required = Join-Path $Runtime $relative
            Assert-NoReparse $required
            if (-not (Test-Path -LiteralPath $required -PathType Leaf)) { throw "Initialized deployment is missing runtime file: $relative" }
        }
        if (-not (Test-Path -LiteralPath $link)) { throw 'Initialized deployment is missing managed data junction' }
        $env:PYTHONNOUSERSITE = '1'
        Invoke-Checked (Join-Path $Runtime 'venv/Scripts/python.exe') @((Join-Path $PSScriptRoot 'initialize-server.py'),'--app-root',$AppRoot,'--data-root',$DataRoot,'--api-port',"$ApiPort",'--database-port',"$DatabasePort",'--service-prefix',$ServicePrefix)
        & (Join-Path $PSScriptRoot 'configure-server-services.ps1') -AppRoot $AppRoot -DataRoot $DataRoot -Action Install
        Write-Host "Server setup finished. Account setup: $DataRoot\config\accounts-setup.json"
        return
    }
}
New-Item -ItemType Directory -Path $DataRoot -Force | Out-Null
Protect-Directory $DataRoot
New-Item -ItemType Directory -Path $Runtime -Force | Out-Null
Protect-Directory $Runtime
New-Item -ItemType Directory -Path (Join-Path $DataRoot 'config') -Force | Out-Null
if (-not (Test-Path -LiteralPath $markerPath)) { Write-Json $markerPath $target }
Write-Json $targetPath $target
if (Test-Path -LiteralPath $link) {
    $item = Get-Item -LiteralPath $link -Force
    if (-not ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -or [IO.Path]::GetFullPath([string]$item.Target[0]).TrimEnd('\') -ne $DataRoot) { throw 'Managed data junction points elsewhere' }
} else { New-Item -ItemType Junction -Path $link -Target $DataRoot | Out-Null }
foreach ($name in @('downloads','uv','python','cache','mamba')) { Assert-NoReparse (Join-Path $Runtime $name); New-Item -ItemType Directory -Path (Join-Path $Runtime $name) -Force | Out-Null }
foreach ($name in @('postgres','venv','micromamba.exe','winsw.exe')) { Assert-NoReparse (Join-Path $Runtime $name) }
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$lock = Get-Content (Join-Path $AppRoot 'deploy/windows/bootstrap-lock.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$uvArchive = Join-Path $Runtime 'downloads/uv.zip'
Get-Verified $lock.artifacts.uv $uvArchive
Expand-Archive -LiteralPath $uvArchive -DestinationPath (Join-Path $Runtime 'uv') -Force
$uv = Join-Path $Runtime 'uv/uv.exe'
if (-not (Test-Path -LiteralPath $uv)) { throw 'uv archive layout changed' }
Get-Verified $lock.artifacts.micromamba (Join-Path $Runtime 'micromamba.exe')
Get-Verified $lock.artifacts.winsw (Join-Path $Runtime 'winsw.exe')
Assert-MicromambaRuntime (Join-Path $Runtime 'micromamba.exe')
$env:UV_CACHE_DIR = Join-Path $Runtime 'cache/uv'
$env:UV_PYTHON_INSTALL_DIR = Join-Path $Runtime 'python'
$env:UV_PYTHON_BIN_DIR = Join-Path $Runtime 'python-bin'
$env:UV_PYTHON_INSTALL_BIN = '0'
$env:UV_LINK_MODE = 'copy'
$env:UV_PROJECT_ENVIRONMENT = Join-Path $Runtime 'venv'
$env:MAMBA_ROOT_PREFIX = Join-Path $Runtime 'mamba'
$env:PYTHONNOUSERSITE = '1'
Invoke-Checked $uv @('python','install',$lock.python_version,'--no-bin','--no-registry','--no-config')
Invoke-Checked $uv @('sync','--project',$AppRoot,'--locked','--no-dev','--no-default-groups','--python',$lock.python_version,'--managed-python','--no-config')
Install-PythonCppRuntime $Runtime
Invoke-Checked (Join-Path $Runtime 'venv/Scripts/python.exe') @('-c','import onnxruntime, fastembed, rapidocr; print(onnxruntime.__version__)')
$mambaAction = 'create'
if (Test-Path -LiteralPath (Join-Path $Runtime 'postgres/conda-meta/history')) { $mambaAction = 'install' }
Invoke-Checked (Join-Path $Runtime 'micromamba.exe') @($mambaAction,'--yes','--no-rc','--prefix',(Join-Path $Runtime 'postgres'),'--file',(Join-Path $AppRoot 'deploy/windows/postgres-explicit.txt'))
Invoke-Checked (Join-Path $Runtime 'postgres/Library/bin/postgres.exe') @('--version')
Invoke-Checked (Join-Path $Runtime 'venv/Scripts/python.exe') @((Join-Path $PSScriptRoot 'initialize-server.py'),'--app-root',$AppRoot,'--data-root',$DataRoot,'--api-port',"$ApiPort",'--database-port',"$DatabasePort",'--service-prefix',$ServicePrefix)
& (Join-Path $PSScriptRoot 'configure-server-services.ps1') -AppRoot $AppRoot -DataRoot $DataRoot -Action Install
Write-Host "Server setup finished. Account setup: $DataRoot\config\accounts-setup.json"
} finally {
    foreach ($mutex in $setupMutexes) { $mutex.ReleaseMutex(); $mutex.Dispose() }
}

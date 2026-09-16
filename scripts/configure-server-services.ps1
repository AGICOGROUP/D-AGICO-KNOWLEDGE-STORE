param(
    [Parameter(Mandatory)][string]$AppRoot,
    [Parameter(Mandatory)][string]$DataRoot,
    [ValidateSet('Install','Start','Stop','Status','Uninstall')][string]$Action = 'Install'
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2

function Get-CanonicalRoot([string]$Path) {
    if ($Path -notmatch '^[A-Za-z]:[\\/]' -or $Path -match '["\r\n]') { throw 'Expected a local absolute directory.' }
    $Full = [IO.Path]::GetFullPath($Path).TrimEnd('\','/')
    if ($Full.Length -le 3) { throw 'A drive root is not allowed.' }
    return $Full
}

function Assert-NoReparseParents([string]$Path) {
    $Current = $Path
    while ($Current) {
        if (Test-Path -LiteralPath $Current) {
            if ((Get-Item -LiteralPath $Current -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Managed paths may not use unowned reparse points.' }
        }
        $Current = Split-Path -Path $Current -Parent
    }
}

function Get-ServiceSid([string]$Name) {
    # Windows service SID: SHA1 of the uppercase UTF-16 service name, five DWORDs.
    $Sha = [Security.Cryptography.SHA1]::Create()
    try { $Hash = $Sha.ComputeHash([Text.Encoding]::Unicode.GetBytes($Name.ToUpperInvariant())) } finally { $Sha.Dispose() }
    $Parts = for ($Index = 0; $Index -lt 20; $Index += 4) { [BitConverter]::ToUInt32($Hash, $Index).ToString() }
    return 'S-1-5-80-' + ($Parts -join '-')
}

function Assert-OwnedService($Service, [string]$Name) {
    $Expected = Join-Path $DataRoot "services/$Name.exe"
    $ImagePath = $Service.PathName.Trim()
    if (($ImagePath -ne ('"' + $Expected + '"') -and $ImagePath -ne $Expected) -or $Service.StartName -ne "NT SERVICE\$Name") {
        throw "Service $Name belongs to another installation or identity; refusing to modify it."
    }
}

function Get-OwnedService([string]$Name) {
    $Service = Get-CimInstance Win32_Service -Filter "Name='$Name'"
    if ($Service) { Assert-OwnedService $Service $Name }
    return $Service
}

function Set-ProtectedAccess([string]$Path, [hashtable]$Grants, [switch]$Children, [switch]$Secret) {
    $Item = Get-Item -LiteralPath $Path -Force
    if ($Item.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Refusing to change permissions on a reparse point.' }
    if ($Item.PSIsContainer) { $Acl = New-Object Security.AccessControl.DirectorySecurity }
    else { $Acl = New-Object Security.AccessControl.FileSecurity }
    $Acl.SetAccessRuleProtection($true, $false)
    $Base = @{'S-1-5-18'='FullControl'; 'S-1-5-32-544'='FullControl'}
    if (-not $Secret) { $Base[[Security.Principal.WindowsIdentity]::GetCurrent().User.Value] = 'FullControl' }
    foreach ($Sid in $Grants.Keys) { $Base[$Sid] = $Grants[$Sid] }
    foreach ($Sid in $Base.Keys) {
        $Identity = New-Object Security.Principal.SecurityIdentifier($Sid)
        $Inheritance = [Security.AccessControl.InheritanceFlags]::None
        if ($Item.PSIsContainer -and ($Children -or -not $Grants.ContainsKey($Sid))) { $Inheritance = [Security.AccessControl.InheritanceFlags]'ContainerInherit, ObjectInherit' }
        $Rule = New-Object Security.AccessControl.FileSystemAccessRule($Identity, [Security.AccessControl.FileSystemRights]$Base[$Sid], $Inheritance, [Security.AccessControl.PropagationFlags]::None, [Security.AccessControl.AccessControlType]::Allow)
        $Acl.AddAccessRule($Rule)
    }
    Set-Acl -LiteralPath $Path -AclObject $Acl
}

function Test-ModelCacheLink($Item, [string]$Root) {
    if ($Item.PSIsContainer -or $Item.LinkType -ne 'SymbolicLink' -or @($Item.Target).Count -ne 1) { return $false }
    $Relative = $Item.FullName.Substring($Root.TrimEnd('\').Length + 1)
    if ($Relative -notmatch '^(models--[^\\]+)\\snapshots\\[a-f0-9]{40}\\[^\\]+$') { return $false }
    $Repository = Join-Path $Root $Matches[1]
    $Target = [string]$Item.Target[0]
    if (-not [IO.Path]::IsPathRooted($Target)) { $Target = Join-Path $Item.DirectoryName $Target }
    $Target = [IO.Path]::GetFullPath($Target)
    $BlobRoot = Join-Path $Repository 'blobs'
    if ((Split-Path $Target -Parent) -ne $BlobRoot -or (Split-Path $Target -Leaf) -notmatch '^([a-f0-9]{40}|[a-f0-9]{64})$') { return $false }
    Assert-NoReparseParents $Target
    return (Test-Path -LiteralPath $Target -PathType Leaf)
}

function Set-ProtectedTree([string]$Path, [hashtable]$Grants, [string]$ManagedPythonVersion = '', [switch]$AllowModelCacheLinks) {
    # uv creates one minor-version alias beside the pinned interpreter. Only that
    # exact local alias is allowed; never recurse through it or accept arbitrary links.
    $ManagedAlias = $null
    $ManagedTarget = $null
    if ($ManagedPythonVersion) {
        if ($ManagedPythonVersion -notmatch '^\d+\.\d+\.\d+$') { throw 'Invalid pinned Python version.' }
        $Minor = ($ManagedPythonVersion.Split('.')[0..1] -join '.')
        $ManagedAlias = Join-Path $Path "cpython-$Minor-windows-x86_64-none"
        $ManagedTarget = Join-Path $Path "cpython-$ManagedPythonVersion-windows-x86_64-none"
    }
    $Pending = New-Object 'Collections.Generic.Stack[string]'
    $Pending.Push($Path)
    while ($Pending.Count) {
        $Directory = $Pending.Pop()
        foreach ($Child in Get-ChildItem -LiteralPath $Directory -Force) {
            if ($Child.Attributes -band [IO.FileAttributes]::ReparsePoint) {
                if ($AllowModelCacheLinks -and (Test-ModelCacheLink $Child $Path)) { continue }
                if ($ManagedAlias -and $Child.FullName -eq $ManagedAlias -and $Child.LinkType -eq 'Junction' -and @($Child.Target).Count -eq 1 -and (Get-CanonicalRoot ([string]$Child.Target[0])) -eq $ManagedTarget) {
                    Assert-NoReparseParents $ManagedTarget
                    if (-not (Test-Path -LiteralPath $ManagedTarget -PathType Container)) { throw 'Pinned Python alias target is missing.' }
                    continue
                }
                throw "Unexpected reparse point in managed tree: $Path"
            }
            if ($Child.PSIsContainer) { $Pending.Push($Child.FullName) }
        }
    }
    Set-ProtectedAccess $Path $Grants -Children
    # Reset descendants to this protected root in one native operation (no recursive data junction traversal).
    foreach ($Child in Get-ChildItem -LiteralPath $Path -Force) {
        if ($Child.Attributes -band [IO.FileAttributes]::ReparsePoint) {
            # /L operates on the alias itself; no /T means no target traversal.
            & "$env:SystemRoot/System32/icacls.exe" $Child.FullName /reset /Q /L | Out-Null
        } else {
            & "$env:SystemRoot/System32/icacls.exe" $Child.FullName /reset /T /Q /L | Out-Null
        }
        if ($LASTEXITCODE -ne 0) { throw 'Could not protect a managed directory.' }
    }
}

function New-ServiceXml([string]$Role) {
    $Name = $Names[$Role]
    $Escape = { param($Value) [Security.SecurityElement]::Escape([string]$Value) }
    $LogPath = & $Escape (Join-Path $DataRoot "logs/$Role")
    $Working = & $Escape $AppRoot
    $TempPath = & $Escape (Join-Path $DataRoot "temp/$Role")
    if ($Role -eq 'database') {
        $Executable = & $Escape (Join-Path $AppRoot '.runtime/postgres/Library/bin/postgres.exe')
        $Cluster = Join-Path $AppRoot '.runtime/data/postgres'
        $Arguments = & $Escape ('-D "' + $Cluster + '" -h 127.0.0.1 -p ' + $Marker.database_port)
        $ArgumentElement = "<startarguments>$Arguments</startarguments>"
        $StopExecutable = & $Escape (Join-Path $AppRoot '.runtime/postgres/Library/bin/pg_ctl.exe')
        $StopArguments = & $Escape ('-D "' + $Cluster + '" -m fast -w -t 60 stop')
        $Extra = "<stopexecutable>$StopExecutable</stopexecutable><stoparguments>$StopArguments</stoparguments>"
    } else {
        $Executable = & $Escape "$env:SystemRoot/System32/WindowsPowerShell/v1.0/powershell.exe"
        $Launcher = Join-Path $AppRoot 'deploy/windows/launch-server.ps1'
        $Arguments = & $Escape ('-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "' + $Launcher + '" -Mode ' + $Role + ' -AppRoot "' + $AppRoot + '" -DataRoot "' + $DataRoot + '" -ApiPort ' + $Marker.api_port + ' -Account "NT SERVICE\' + $Name + '"')
        $ArgumentElement = "<arguments>$Arguments</arguments>"
        $Extra = '<depend>' + $Names.database + '</depend>'
    }
    return @"
<service>
  <id>$Name</id><name>AGICO Knowledge $Role ($($Marker.service_prefix))</name>
  <description>Owned AGICO knowledge server service.</description>
  <executable>$Executable</executable>$ArgumentElement
  <workingdirectory>$Working</workingdirectory>
  <env name="TEMP" value="$TempPath"/><env name="TMP" value="$TempPath"/>
  <logpath>$LogPath</logpath><log mode="roll-by-size"><sizeThreshold>10240</sizeThreshold><keepFiles>5</keepFiles></log>
  <startmode>Automatic</startmode><stoptimeout>75sec</stoptimeout>
  <onfailure action="restart" delay="10sec"/><resetfailure>1hour</resetfailure>
  $Extra
</service>
"@
}

function Invoke-Sc([string[]]$ScArguments) {
    # Explicit Windows argv quoting also preserves quotes in binPath under Windows PowerShell 5.1.
    $Quoted = foreach ($Argument in $ScArguments) {
        '"' + (($Argument -replace '(\\*)"', '$1$1\"') -replace '(\\+)$', '$1$1') + '"'
    }
    $Info = New-Object Diagnostics.ProcessStartInfo
    $Info.FileName = "$env:SystemRoot/System32/sc.exe"
    $Info.Arguments = $Quoted -join ' '
    $Info.UseShellExecute = $false
    $Info.CreateNoWindow = $true
    $Info.RedirectStandardOutput = $true
    $Info.RedirectStandardError = $true
    $Process = [Diagnostics.Process]::Start($Info)
    $null = $Process.StandardOutput.ReadToEnd()
    $null = $Process.StandardError.ReadToEnd()
    $Process.WaitForExit()
    $ExitCode = $Process.ExitCode
    $Process.Dispose()
    if ($ExitCode -ne 0) { throw 'Windows service registration operation failed.' }
}

function Stop-OwnedServices {
    foreach ($Role in @('worker','api','database')) {
        $Name = $Names[$Role]
        $Service = Get-OwnedService $Name
        if ($Service -and $Service.State -ne 'Stopped') {
            Stop-Service -Name $Name -ErrorAction Stop
            (Get-Service $Name).WaitForStatus('Stopped', [TimeSpan]::FromSeconds(90))
        }
    }
}

function Start-OwnedServices {
    foreach ($Role in @('database','api','worker')) {
        $Name = $Names[$Role]
        if (-not (Get-OwnedService $Name)) { throw "Service $Name is not installed." }
        Start-Service -Name $Name
        (Get-Service $Name).WaitForStatus('Running', [TimeSpan]::FromSeconds(45))
        if ($Role -eq 'database') {
            $Ready = $false
            for ($Attempt = 0; $Attempt -lt 30; $Attempt++) {
                & (Join-Path $AppRoot '.runtime/postgres/Library/bin/pg_isready.exe') -h 127.0.0.1 -p $Marker.database_port -t 1 *> $null
                if ($LASTEXITCODE -eq 0) { $Ready = $true; break }
                Start-Sleep -Seconds 1
            }
            if (-not $Ready) { throw 'Database readiness timed out.' }
        }
        if ($Role -eq 'api') {
            $Ready = $false
            for ($Attempt = 0; $Attempt -lt 30; $Attempt++) {
                try {
                    $Response = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:$($Marker.api_port)/health/ready" -TimeoutSec 2
                    if ($Response.StatusCode -eq 200) { $Ready = $true; break }
                } catch { }
                Start-Sleep -Seconds 1
            }
            if (-not $Ready) { throw 'API readiness timed out; inspect role logs.' }
        }
    }
}

$AppRoot = Get-CanonicalRoot $AppRoot
$DataRoot = Get-CanonicalRoot $DataRoot
if ($AppRoot -match '[^\x20-\x7e]' -or $AppRoot -eq $DataRoot -or $DataRoot.StartsWith($AppRoot + '\', [StringComparison]::OrdinalIgnoreCase) -or $AppRoot.StartsWith($DataRoot + '\', [StringComparison]::OrdinalIgnoreCase)) { throw 'Invalid app/data folder boundary.' }
Assert-NoReparseParents $AppRoot
Assert-NoReparseParents $DataRoot
$MarkerPath = Join-Path $DataRoot 'config/deployment.json'
Assert-NoReparseParents $MarkerPath
$Marker = Get-Content -LiteralPath $MarkerPath -Raw -Encoding UTF8 | ConvertFrom-Json
$MarkerKeys = @('schema_version','app_root','data_root','api_port','database_port','service_prefix','stage')
if (@($Marker.PSObject.Properties).Count -ne $MarkerKeys.Count -or @($Marker.PSObject.Properties.Name | Where-Object { $_ -notin $MarkerKeys }).Count -or $Marker.api_port -isnot [int] -or $Marker.database_port -isnot [int]) { throw 'Unexpected deployment marker schema.' }
if ($Marker.schema_version -ne 1 -or (Get-CanonicalRoot $Marker.app_root) -ne $AppRoot -or (Get-CanonicalRoot $Marker.data_root) -ne $DataRoot -or $Marker.service_prefix -notmatch '^[A-Za-z][A-Za-z0-9_]{2,40}$' -or $Marker.api_port -lt 1024 -or $Marker.api_port -gt 65535 -or $Marker.database_port -lt 1024 -or $Marker.database_port -gt 65535 -or $Marker.api_port -eq $Marker.database_port -or $Marker.stage -notin @('initialized','installed')) { throw 'Deployment marker is invalid or bound to another installation.' }
$Names = @{ database = $Marker.service_prefix + 'Database'; api = $Marker.service_prefix + 'Api'; worker = $Marker.service_prefix + 'Worker' }
# Validate ALL registrations before any changes, including ordered stop/uninstall.
foreach ($Name in $Names.Values) { $null = Get-OwnedService $Name }
if ($Action -eq 'Status') {
    foreach ($Role in @('database','api','worker')) {
        $Service = Get-OwnedService $Names[$Role]
        [pscustomobject]@{ Name = $Names[$Role]; State = $(if ($Service) { $Service.State } else { 'NotInstalled' }) }
    }
    return
}
$Principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $Principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'Run service management from an elevated PowerShell.' }
if ($Action -eq 'Stop') { Stop-OwnedServices; return }
if ($Action -eq 'Uninstall') {
    Stop-OwnedServices
    foreach ($Role in @('worker','api','database')) {
        if (Get-OwnedService $Names[$Role]) { Invoke-Sc @('delete', $Names[$Role]) }
    }
    return
}
if ($Action -eq 'Start') { Start-OwnedServices; return }

$Junction = Get-Item -LiteralPath (Join-Path $AppRoot '.runtime/data') -Force
if (-not ($Junction.Attributes -band [IO.FileAttributes]::ReparsePoint) -or (Get-CanonicalRoot ([string]@($Junction.Target)[0])) -ne $DataRoot) { throw 'Managed data junction does not match deployment.' }
foreach ($Relative in @('.runtime/winsw.exe','.runtime/venv/Scripts/python.exe','.runtime/postgres/Library/bin/postgres.exe','deploy/windows/launch-server.ps1')) {
    if (-not (Test-Path -LiteralPath (Join-Path $AppRoot $Relative) -PathType Leaf)) { throw 'Required service dependency is missing.' }
}
$Runtime = Get-Content -LiteralPath (Join-Path $DataRoot 'config/runtime.json') -Raw -Encoding UTF8 | ConvertFrom-Json
if ($Runtime.PSObject.Properties.Name -contains 'AGICO_KB_MAINTENANCE_URL' -or $Runtime.AGICO_KB_MODEL_OFFLINE -ne 'true' -or -not $Runtime.AGICO_KB_EXPECTED_MODEL_IDENTITY) { throw 'Runtime configuration must contain only provisioned runtime credentials.' }
if ((Get-CanonicalRoot $Runtime.AGICO_KB_STORAGE_ROOT) -ne (Join-Path $DataRoot 'originals') -or (Get-CanonicalRoot $Runtime.AGICO_KB_MODEL_CACHE) -ne (Join-Path $DataRoot 'models')) { throw 'Runtime paths must belong to this deployment.' }
foreach ($Relative in @('config/runtime.json','services','config/api.json','config/worker.json','logs','temp','models','originals','postgres')) {
    Assert-NoReparseParents (Join-Path $DataRoot $Relative)
}
$ReadGrants = @{}; $TraverseGrants = @{}; $Sids = @{}
foreach ($Role in $Names.Keys) {
    $Sids[$Role] = Get-ServiceSid $Names[$Role]
    $ReadGrants[$Sids[$Role]] = 'ReadAndExecute'
    $TraverseGrants[$Sids[$Role]] = 'Traverse'
}
# Never recurse through AppRoot: it can contain another development/preview deployment.
foreach ($Directory in @($AppRoot, (Join-Path $AppRoot '.runtime'))) {
    $Acl = Get-Acl -LiteralPath $Directory
    foreach ($Sid in $Sids.Values) {
        $Rule = New-Object Security.AccessControl.FileSystemAccessRule((New-Object Security.Principal.SecurityIdentifier($Sid)), 'ReadAndExecute', 'Allow')
        $Acl.SetAccessRule($Rule)
        # Deny this-folder writes even if a drive grants Authenticated Users modify.
        # This rule does not inherit into existing preview folders.
        $Deny = New-Object Security.AccessControl.FileSystemAccessRule((New-Object Security.Principal.SecurityIdentifier($Sid)), 'Write, Delete, DeleteSubdirectoriesAndFiles', 'Deny')
        $Acl.AddAccessRule($Deny)
    }
    Set-Acl -LiteralPath $Directory -AclObject $Acl
}
foreach ($Relative in @('src','deploy','scripts','.runtime/uv','.runtime/venv','.runtime/python','.runtime/postgres')) {
    $Path = Join-Path $AppRoot $Relative
    if (Test-Path -LiteralPath $Path) {
        if ($Relative -eq '.runtime/python') {
            $Lock = Get-Content -LiteralPath (Join-Path $AppRoot 'deploy/windows/bootstrap-lock.json') -Raw -Encoding UTF8 | ConvertFrom-Json
            Set-ProtectedTree $Path $ReadGrants -ManagedPythonVersion $Lock.python_version
        } else { Set-ProtectedTree $Path $ReadGrants }
    }
}
foreach ($File in Get-ChildItem -LiteralPath (Join-Path $AppRoot '.runtime') -File -Force) {
    Set-ProtectedAccess $File.FullName $ReadGrants
}
Set-ProtectedAccess $DataRoot $TraverseGrants
foreach ($Relative in @('config','services','logs','temp')) {
    $Path = Join-Path $DataRoot $Relative
    $null = New-Item -ItemType Directory -Path $Path -Force
    Set-ProtectedAccess $Path $TraverseGrants
}
Set-ProtectedTree (Join-Path $DataRoot 'models') @{ $Sids.api = 'ReadAndExecute'; $Sids.worker = 'ReadAndExecute' } -AllowModelCacheLinks
Set-ProtectedTree (Join-Path $DataRoot 'originals') @{ $Sids.api = 'Modify'; $Sids.worker = 'Modify' }
Set-ProtectedTree (Join-Path $DataRoot 'postgres') @{ $Sids.database = 'Modify' }
foreach ($Role in @('database','api','worker')) {
    $Name = $Names[$Role]
    $Existing = Get-OwnedService $Name
    foreach ($Relative in @("logs/$Role", "temp/$Role")) {
        $Path = Join-Path $DataRoot $Relative
        $null = New-Item -ItemType Directory -Path $Path -Force
        Set-ProtectedTree $Path @{ $Sids[$Role] = 'Modify' }
    }
    if ($Role -ne 'database') {
        $ConfigFile = Join-Path $DataRoot "config/$Role.json"
        if (-not (Test-Path -LiteralPath $ConfigFile)) {
            $null = New-Item -ItemType File -Path $ConfigFile
            Set-ProtectedAccess $ConfigFile @{ $Sids[$Role] = 'Read' } -Secret
            [IO.File]::WriteAllText($ConfigFile, ($Runtime | ConvertTo-Json), (New-Object Text.UTF8Encoding($false)))
        } else { Set-ProtectedAccess $ConfigFile @{ $Sids[$Role] = 'Read' } -Secret }
    }
    $Wrapper = Join-Path $DataRoot "services/$Name.exe"
    $XmlPath = Join-Path $DataRoot "services/$Name.xml"
    if (-not $Existing) {
        Copy-Item -LiteralPath (Join-Path $AppRoot '.runtime/winsw.exe') -Destination $Wrapper -Force
    }
    [IO.File]::WriteAllText($XmlPath, (New-ServiceXml $Role), (New-Object Text.UTF8Encoding($false)))
    Set-ProtectedAccess $Wrapper @{ $Sids[$Role] = 'ReadAndExecute' }
    Set-ProtectedAccess $XmlPath @{ $Sids[$Role] = 'Read' }
    if (-not $Existing) {
        # Register directly as the passwordless virtual account. Never install as LocalSystem.
        # https://learn.microsoft.com/windows-server/identity/ad-ds/manage/understand-service-accounts
        $Create = @('create', $Name, 'binPath=', ('"' + $Wrapper + '"'), 'start=', 'auto', 'obj=', "NT SERVICE\$Name", 'DisplayName=', "AGICO Knowledge $Role ($($Marker.service_prefix))")
        if ($Role -ne 'database') { $Create += @('depend=', $Names.database) }
        Invoke-Sc $Create
    }
    $null = Get-OwnedService $Name
    Invoke-Sc @('failure', $Name, 'reset=', '3600', 'actions=', 'restart/10000/restart/10000/restart/10000')
}
Start-OwnedServices
$Marker.stage = 'installed'
[IO.File]::WriteAllText($MarkerPath, ($Marker | ConvertTo-Json), (New-Object Text.UTF8Encoding($false)))
Write-Output 'Server services installed and ready.'

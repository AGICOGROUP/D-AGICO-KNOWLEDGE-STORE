$ErrorActionPreference = 'Stop'
$Source = Join-Path (Split-Path $PSScriptRoot) 'scripts/configure-server-services.ps1'
if (-not (Test-Path $Source)) { throw 'Service implementation is missing.' }
$Tokens = $null; $Errors = $null
$Ast = [Management.Automation.Language.Parser]::ParseFile($Source, [ref]$Tokens, [ref]$Errors)
if ($Errors.Count) { throw 'Service script does not parse.' }
foreach ($Function in $Ast.FindAll({ param($Node) $Node -is [Management.Automation.Language.FunctionDefinitionAst] }, $false)) {
    . ([scriptblock]::Create($Function.Extent.Text))
}
function Assert-True($Condition, $Message) { if (-not $Condition) { throw $Message } }
$AppRoot = 'C:\Agico App'; $DataRoot = 'D:\Company & Data'
$Marker = [pscustomobject]@{ api_port = 18765; database_port = 25432; service_prefix = 'FixtureKb' }
$Names = @{ database = 'FixtureKbDatabase'; api = 'FixtureKbApi'; worker = 'FixtureKbWorker' }
foreach ($Role in @('database','api','worker')) {
    [xml]$Xml = New-ServiceXml $Role
    Assert-True ($Xml.service.id -eq $Names[$Role]) 'Wrong service ID.'
    Assert-True ($Xml.service.logpath -eq (Join-Path $DataRoot "logs/$Role")) 'Wrong log boundary.'
    if ($Role -eq 'database') {
        Assert-True ($Xml.service.executable.EndsWith('postgres.exe')) 'Database must use postgres directly.'
        Assert-True ($Xml.service.stoparguments.Contains('-m fast -w -t 60 stop')) 'Database needs graceful shutdown.'
        Assert-True ($Xml.service.startarguments -and $Xml.service.startarguments.Contains('25432')) 'WinSW stoparguments requires startarguments, including database port.'
    } else {
        Assert-True ($Xml.service.depend -eq 'FixtureKbDatabase') 'Database dependency missing.'
        Assert-True ($Xml.service.arguments.Contains('18765')) 'Configured API port omitted.'
        Assert-True (-not $Xml.OuterXml.Contains('DATABASE_URL')) 'Credentials must never appear in XML.'
    }
}
Assert-True ((Get-ServiceSid 'FixtureKbApi') -match '^S-1-5-80-\d+-\d+-\d+-\d+-\d+$') 'Virtual service SID must be stable and valid.'
Assert-True ((Get-ServiceSid 'FixtureKbApi') -eq (Get-ServiceSid 'fixturekbapi')) 'Service SID names are case-insensitive.'
$Expected = Join-Path $DataRoot 'services/FixtureKbApi.exe'
Assert-OwnedService ([pscustomobject]@{ PathName = '"' + $Expected + '"'; StartName = 'NT SERVICE\FixtureKbApi' }) 'FixtureKbApi'
foreach ($Foreign in @(
    [pscustomobject]@{ PathName = '"C:\foreign.exe"'; StartName = 'NT SERVICE\FixtureKbApi' },
    [pscustomobject]@{ PathName = '"' + $Expected + '"'; StartName = 'LocalSystem' },
    [pscustomobject]@{ PathName = '"' + $Expected + '" --other'; StartName = 'NT SERVICE\FixtureKbApi' }
)) {
    $Rejected = $false
    try { Assert-OwnedService $Foreign 'FixtureKbApi' } catch { $Rejected = $true }
    Assert-True $Rejected 'Foreign registration must be rejected before mutation.'
}
$FixtureRoot = Join-Path ([IO.Path]::GetTempPath()) ('agico-service-acl-' + [guid]::NewGuid().ToString('N'))
$null = New-Item -ItemType Directory -Path $FixtureRoot
try {
    $ApiSid = Get-ServiceSid 'FixtureKbApi'
    # Existing bootstrap secrets inherit operator ACLs. Adding traverse-only service
    # access to parent directories must keep admin inheritance and not expose secrets.
    Set-ProtectedAccess $FixtureRoot @{} -Children
    $ExistingConfigDirectory = Join-Path $FixtureRoot 'config'
    $null = New-Item -ItemType Directory -Path $ExistingConfigDirectory
    $ExistingSecret = Join-Path $ExistingConfigDirectory 'initial-access.json'
    [IO.File]::WriteAllText($ExistingSecret, 'private-fixture')
    Set-ProtectedAccess $FixtureRoot @{ $ApiSid = 'Traverse' }
    Set-ProtectedAccess $ExistingConfigDirectory @{ $ApiSid = 'Traverse' }
    Assert-True (([IO.File]::ReadAllText($ExistingSecret)) -eq 'private-fixture') 'Operator lost access to an inherited bootstrap secret.'
    $SecretRules = (Get-Acl -LiteralPath $ExistingSecret).Access
    Assert-True (@($SecretRules).Count -ge 2) 'Inherited operator/admin permissions disappeared.'
    foreach ($Rule in $SecretRules) {
        Assert-True ($Rule.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value -ne $ApiSid) 'Traverse-only parent access leaked a bootstrap secret.'
    }
    $Config = Join-Path $FixtureRoot 'api.json'
    [IO.File]::WriteAllText($Config, '{}')
    Set-ProtectedAccess $Config @{ $ApiSid = 'Read' } -Secret
    $Acl = Get-Acl -LiteralPath $Config
    Assert-True $Acl.AreAccessRulesProtected 'Secret config inheritance must be disabled.'
    foreach ($Rule in $Acl.Access) {
        $Sid = $Rule.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value
        Assert-True ($Sid -in @($ApiSid,'S-1-5-18','S-1-5-32-544')) 'Secret config leaks to another identity.'
        if ($Sid -eq $ApiSid) { Assert-True (-not ($Rule.FileSystemRights -band [Security.AccessControl.FileSystemRights]::Write)) 'Service may not modify its config.' }
    }
    $Tree = Join-Path $FixtureRoot 'models'
    $null = New-Item -ItemType Directory -Path (Join-Path $Tree 'nested') -Force
    $ModelFile = Join-Path $Tree 'nested/model.txt'
    [IO.File]::WriteAllText($ModelFile, 'fixture')
    Set-ProtectedTree $Tree @{ $ApiSid = 'ReadAndExecute' }
    $Rule = (Get-Acl $ModelFile).Access | Where-Object { $_.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value -eq $ApiSid }
    Assert-True ($null -ne $Rule) 'Model read access must inherit to descendants.'
    Assert-True (-not ($Rule.FileSystemRights -band [Security.AccessControl.FileSystemRights]::Write)) 'Models must remain read-only.'
    $PythonTree = Join-Path $FixtureRoot 'python'
    $PythonTarget = Join-Path $PythonTree 'cpython-3.12.14-windows-x86_64-none'
    $null = New-Item -ItemType Directory -Path $PythonTarget -Force
    $Alias = Join-Path $PythonTree 'cpython-3.12-windows-x86_64-none'
    $null = New-Item -ItemType Junction -Path $Alias -Target $PythonTarget
    try {
        Set-ProtectedTree $PythonTree @{ $ApiSid = 'ReadAndExecute' } -ManagedPythonVersion '3.12.14'
        Assert-True ((Get-Item -LiteralPath $Alias).LinkType -eq 'Junction') 'Managed Python alias was altered.'
        $Rejected = $false
        try { Set-ProtectedTree $PythonTree @{ $ApiSid = 'ReadAndExecute' } -ManagedPythonVersion '3.12.13' } catch { $Rejected = $true }
        Assert-True $Rejected 'A junction outside the pinned Python version must be rejected.'
    } finally { [IO.Directory]::Delete($Alias) }
    $Cache = Join-Path $FixtureRoot 'cache'
    $Repo = Join-Path $Cache 'models--fixture--model'
    $Snapshot = Join-Path $Repo ('snapshots/' + ('a' * 40))
    $null = New-Item -ItemType Directory -Path $Snapshot -Force
    $null = New-Item -ItemType Directory -Path (Join-Path $Repo 'blobs') -Force
    $Blob = Join-Path $Repo ('blobs/' + ('b' * 40))
    [IO.File]::WriteAllText($Blob, 'model')
    $ModelLink = Join-Path $Snapshot 'config.json'
    $null = New-Item -ItemType SymbolicLink -Path $ModelLink -Target $Blob
    try {
        Set-ProtectedTree $Cache @{ $ApiSid = 'ReadAndExecute' } -AllowModelCacheLinks
        Assert-True (([IO.File]::ReadAllText($ModelLink)) -eq 'model') 'Internal model blob link must remain usable.'
    } finally { [IO.File]::Delete($ModelLink) }
    $null = New-Item -ItemType SymbolicLink -Path $ModelLink -Target $Config
    try {
        $Rejected = $false
        try { Set-ProtectedTree $Cache @{ $ApiSid = 'ReadAndExecute' } -AllowModelCacheLinks } catch { $Rejected = $true }
        Assert-True $Rejected 'Model snapshot links may not escape to an unrelated file.'
    } finally { [IO.File]::Delete($ModelLink) }
} finally {
    if ([IO.Path]::GetFullPath($FixtureRoot).StartsWith([IO.Path]::GetFullPath([IO.Path]::GetTempPath()), [StringComparison]::OrdinalIgnoreCase)) {
        Remove-Item -LiteralPath $FixtureRoot -Recurse -Force
    }
}
Write-Output 'Windows server service fixtures passed.'

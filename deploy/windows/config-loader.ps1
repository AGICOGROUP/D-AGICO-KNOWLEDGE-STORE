function Import-ProtectedKbConfig {
    param([Parameter(Mandatory)][string]$ConfigFile, [Parameter(Mandatory)][string]$Account)
    if (-not [IO.Path]::IsPathRooted($ConfigFile)) { throw 'Configuration path must be absolute.' }
    $Acl = Get-Acl -LiteralPath $ConfigFile
    if (-not $Acl.AreAccessRulesProtected) { throw 'Configuration ACL must have inheritance disabled.' }
    $AccountSid = ([Security.Principal.NTAccount]::new($Account)).Translate([Security.Principal.SecurityIdentifier]).Value
    $Allowed = @('S-1-5-18', 'S-1-5-32-544', $AccountSid)
    foreach ($Rule in $Acl.Access) {
        $Sid = $Rule.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value
        if ($Rule.AccessControlType -eq 'Allow' -and $Sid -notin $Allowed) { throw 'Configuration grants access outside service account, SYSTEM, Administrators.' }
    }
    $Config = Get-Content -LiteralPath $ConfigFile -Raw -Encoding UTF8 | ConvertFrom-Json
    $Keys = @('AGICO_KB_DATABASE_URL','AGICO_KB_MAINTENANCE_URL','AGICO_KB_STORAGE_ROOT','AGICO_KB_SCHEMA','AGICO_KB_MODEL_CACHE','AGICO_KB_MODEL_OFFLINE','AGICO_KB_EXPECTED_MODEL_IDENTITY','AGICO_KB_EMBEDDING_MODEL','AGICO_KB_ALLOWED_HOSTS')
    foreach ($Property in $Config.PSObject.Properties) {
        if ($Property.Name -notin $Keys -or $Property.Value -isnot [string]) { throw 'Unsupported runtime configuration key or type.' }
        [Environment]::SetEnvironmentVariable($Property.Name, $Property.Value, 'Process')
    }
}

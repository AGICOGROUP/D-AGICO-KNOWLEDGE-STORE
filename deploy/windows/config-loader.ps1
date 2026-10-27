function Read-ProtectedKbJson {
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
    return (Get-Content -LiteralPath $ConfigFile -Raw -Encoding UTF8 | ConvertFrom-Json)
}

function Import-ProtectedKbConfig {
    param([Parameter(Mandatory)][string]$ConfigFile, [Parameter(Mandatory)][string]$Account)
    $Config = Read-ProtectedKbJson $ConfigFile $Account
    $Keys = @('AGICO_KB_DATABASE_URL','AGICO_KB_MAINTENANCE_URL','AGICO_KB_STORAGE_ROOT','AGICO_KB_SCHEMA','AGICO_KB_MODEL_CACHE','AGICO_KB_MODEL_OFFLINE','AGICO_KB_EXPECTED_MODEL_IDENTITY','AGICO_KB_EMBEDDING_MODEL','AGICO_KB_ALLOWED_HOSTS','AGICO_KB_PUBLIC_URL','AGICO_KB_VISION_CONFIG_FILE','AGICO_KB_VISION_API_URL','AGICO_KB_VISION_FALLBACK_CONFIG_FILE','AGICO_KB_VISION_FALLBACK_API_URL','AGICO_KB_VISION_MAX_CALLS_PER_DAY','AGICO_KB_VISION_MAX_IMAGES_PER_FILE','AGICO_KB_VISION_FALLBACK_MODEL','AGICO_KB_VISION_PDF_MODEL','AGICO_KB_CAD_CONVERTER','AGICO_KB_OFFICE_CONVERTER','AGICO_KB_BLENDER','AGICO_KB_WORKER_CONCURRENCY')
    foreach ($Property in $Config.PSObject.Properties) {
        if ($Property.Name -notin ($Keys + @('AGICO_KB_API_KEYS_DIR','AGICO_KB_NAMING_FALLBACK_MODELS','AGICO_KB_DINGTALK_CONFIG_FILE')) -or $Property.Value -isnot [string]) { throw 'Unsupported runtime configuration key or type.' }
        [Environment]::SetEnvironmentVariable($Property.Name, $Property.Value, 'Process')
    }
}

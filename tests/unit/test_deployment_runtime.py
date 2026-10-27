import os
import subprocess

import pytest

from agico_kb.convert import find_soffice


def test_legacy_office_uses_same_configured_converter_as_preview(tmp_path, monkeypatch):
    converter = tmp_path / 'custom-soffice.exe'
    converter.write_bytes(b'fresh executable fixture')
    monkeypatch.setenv('AGICO_KB_OFFICE_CONVERTER', str(converter))
    assert find_soffice() == converter


@pytest.mark.skipif(os.name != 'nt', reason='Native Windows config and ACL verification')
def test_service_config_refresh_and_admin_only_master(tmp_path):
    # Only load project function definitions; never invoke service installation.
    script = tmp_path / 'check.ps1'
    script.write_text(r'''
$ErrorActionPreference = 'Stop'
$Tokens = $null; $Errors = $null
$Ast = [Management.Automation.Language.Parser]::ParseFile((Join-Path $PWD 'scripts/configure-server-services.ps1'), [ref]$Tokens, [ref]$Errors)
if ($Errors.Count) { throw 'PowerShell parse errors' }
foreach ($Function in $Ast.FindAll({param($Node) $Node -is [Management.Automation.Language.FunctionDefinitionAst]}, $false)) {
    Invoke-Expression $Function.Extent.Text
}
. ./deploy/windows/config-loader.ps1
$Root = $PSScriptRoot
$Master = Join-Path $Root 'runtime.json'
$Runtime = @{AGICO_KB_STORAGE_ROOT=(Join-Path $Root 'originals');AGICO_KB_DATABASE_URL='fresh-fixture'; AGICO_KB_PUBLIC_URL='https://first.example.test'; AGICO_KB_OFFICE_CONVERTER='fixture'; AGICO_KB_BLENDER='fixture'; AGICO_KB_WORKER_CONCURRENCY='3'}
$Runtime.AGICO_KB_NAMING_FALLBACK_MODELS = 'glm-5.3-flashx,glm-5.3-flash,deepseek-v4-flash'
$Vision = Join-Path $Root 'vision-fixture.txt'
[IO.File]::WriteAllText($Vision, "api=fresh-test-credential`nmodel=glm-5.3-flash")
$Runtime.AGICO_KB_VISION_CONFIG_FILE = $Vision
$DingTalk = Join-Path $Root 'dingtalk-fixture.json'
[IO.File]::WriteAllText($DingTalk, '{"app_secret":"fresh-synthetic-only"}')
$Runtime.AGICO_KB_DINGTALK_CONFIG_FILE = $DingTalk
[IO.File]::WriteAllText($Master, ($Runtime | ConvertTo-Json))
$Sids = @{api=(Get-ServiceSid 'AgicoFreshFixtureApi');worker=(Get-ServiceSid 'AgicoFreshFixtureWorker')}
Update-ProtectedRuntimeConfigs $Master $Sids -PythonExe (Join-Path $PWD '.venv/Scripts/python.exe')
$Runtime = Get-Content $Master -Raw | ConvertFrom-Json
$Vision = Join-Path $Root 'api-keys/primary.txt'
$Runtime.AGICO_KB_PUBLIC_URL = 'https://changed.example.test'
[IO.File]::WriteAllText($Master, ($Runtime | ConvertTo-Json))
Update-ProtectedRuntimeConfigs $Master $Sids -PythonExe (Join-Path $PWD '.venv/Scripts/python.exe')
Import-ProtectedKbConfig $Master ([Security.Principal.WindowsIdentity]::GetCurrent().Name)
if ($env:AGICO_KB_PUBLIC_URL -ne 'https://changed.example.test') { throw 'Master not loadable' }
foreach ($Role in @('api','worker')) {
    $File = Join-Path $Root "$Role.json"
    if ((Get-Content $File -Raw | ConvertFrom-Json).AGICO_KB_PUBLIC_URL -ne $Runtime.AGICO_KB_PUBLIC_URL) { throw 'Stale service config' }
    $Acl = Get-Acl $File
    if (-not $Acl.AreAccessRulesProtected) { throw 'Unprotected service config' }
    $Allowed = @('S-1-5-18','S-1-5-32-544',$Sids[$Role])
    foreach ($Rule in $Acl.Access) {
        $Sid = $Rule.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value
        if ($Sid -notin $Allowed) { throw 'Unexpected service access' }
        if ($Sid -eq $Sids[$Role] -and ($Rule.FileSystemRights -band [Security.AccessControl.FileSystemRights]::Write)) { throw 'Service may write secrets' }
    }
}
foreach ($Rule in (Get-Acl $Master).Access) {
    if ($Rule.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value -notin @('S-1-5-18','S-1-5-32-544')) { throw 'Master exposed to service' }
}
if (-not (Get-Acl $Vision).AreAccessRulesProtected) { throw 'Vision credentials not protected' }
if (-not (Get-Acl $DingTalk).AreAccessRulesProtected) { throw 'DingTalk credentials not protected' }
$VisionSids = @((Get-Acl $Vision).Access | ForEach-Object { $_.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value })
if ($Sids.api -notin $VisionSids -or $Sids.worker -notin $VisionSids) { throw 'Vision credentials not readable by runtime' }
$MasterMaterial = Join-Path $Root '.api-key-encryption.key'
if (-not (Test-Path $MasterMaterial)) { throw 'Missing server encryption material' }
foreach ($Role in @('api','worker')) {
    $KeyRule = @((Get-Acl $MasterMaterial).Access | Where-Object { $_.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value -eq $Sids[$Role] })
    if ($KeyRule.Count -ne 1 -or ($KeyRule[0].FileSystemRights -band [Security.AccessControl.FileSystemRights]::Write)) { throw 'Incorrect encryption material permissions' }
}
$KeyAcl = Get-Acl (Join-Path $Root 'api-keys')
foreach ($Role in @('api','worker')) {
    $Rules = @($KeyAcl.Access | Where-Object { $_.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value -eq $Sids[$Role] })
    if ($Rules.Count -eq 0) { throw 'Missing key directory service access' }
    $Writable = [bool]($Rules[0].FileSystemRights -band [Security.AccessControl.FileSystemRights]::Write)
    if (($Role -eq 'api') -ne $Writable) { throw 'Incorrect key directory write permissions' }
}
'Native config and ACL check passed'
''', encoding='utf-8')
    # A PowerShell 7 host exports its module path to children. Keep the required
    # Windows PowerShell 5.1 test on its own native modules.
    env = {**os.environ, 'PSModulePath': os.environ['SystemRoot'] + r'\System32\WindowsPowerShell\v1.0\Modules'}
    result = subprocess.run(['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(script)], env=env, capture_output=True, text=True, timeout=30, check=False)
    assert result.returncode == 0, result.stdout + result.stderr

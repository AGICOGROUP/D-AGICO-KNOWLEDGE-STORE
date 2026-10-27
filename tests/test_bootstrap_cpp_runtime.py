"""Detect incompatible C++ runtime before launching dependency installers."""
import os
import shutil
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows PowerShell version-resource checks')
@pytest.mark.parametrize('version,accepted', [('14.36.32532.0', False), ('14.51.36247.0', True)])
def test_bootstrap_checks_app_local_cpp_runtime_before_launch(tmp_path, version, accepted):
    source = Path(__file__).resolve().parents[1]/'scripts/bootstrap-server.ps1'
    script = r'''
$ErrorActionPreference='Stop'
$tokens=$null; $errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile($env:BOOTSTRAP_SOURCE,[ref]$tokens,[ref]$errors)
$helper=$ast.Find({param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Assert-MicromambaRuntime'},$true)
if(-not $helper){throw 'C++ runtime preflight is missing'}
Invoke-Expression $helper.Extent.Text
$dll=Join-Path $env:CPP_TEST_ROOT 'msvcp140.dll'
$code='using System.Reflection; [assembly: AssemblyFileVersion("'+$env:CPP_VERSION+'")] public class SyntheticRuntime { }'
Add-Type -TypeDefinition $code -OutputAssembly $dll
foreach($name in @('vcruntime140.dll','vcruntime140_1.dll')) {Copy-Item -LiteralPath $dll -Destination (Join-Path $env:CPP_TEST_ROOT $name)}
try {
 Assert-MicromambaRuntime (Join-Path $env:CPP_TEST_ROOT 'micromamba.exe')
 if($env:CPP_ACCEPTED -ne 'true'){throw 'Old runtime was not rejected'}
} catch {
 if($env:CPP_ACCEPTED -eq 'false' -and $_.Exception.Message -like '*C++ runtime*'){exit 0}
 throw
}
'''
    run=subprocess.run(['powershell.exe','-NoProfile','-Command',script],
        env={**os.environ,'BOOTSTRAP_SOURCE':str(source),'CPP_TEST_ROOT':str(tmp_path),
             'CPP_VERSION':version,'CPP_ACCEPTED':str(accepted).lower()},
        capture_output=True,text=True,errors='replace',timeout=30)
    assert run.returncode==0,run.stdout+run.stderr


def test_cpp_runtime_check_precedes_python_and_database_installs():
    source=(Path(__file__).resolve().parents[1]/'scripts/bootstrap-server.ps1').read_text(encoding='utf-8')
    check=source.index("Assert-MicromambaRuntime (Join-Path $Runtime 'micromamba.exe')")
    assert check<source.index("@('python','install'")
    assert check<source.index("@($mambaAction,'--yes'")


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows Python runtime placement')
@pytest.mark.parametrize('owned_base',[True,False])
def test_python_cpp_runtime_covers_base_and_venv_but_rejects_external_python(tmp_path,owned_base):
    runtime=tmp_path/'app/.runtime';runtime.mkdir(parents=True)
    if owned_base:
        base=runtime/'python/base'
        shutil.copytree(Path(sys._base_executable).parent,base)
        python=base/'python.exe'
    else:
        python=Path(sys._base_executable)
    venv=runtime/'venv'
    subprocess.run([str(python),'-m','venv','--without-pip',str(venv)],check=True,capture_output=True,timeout=30)
    for name in ('msvcp140.dll','vcruntime140.dll','vcruntime140_1.dll'):
        (runtime/name).write_bytes(b'fresh synthetic runtime copy '+name.encode())
    source=Path(__file__).resolve().parents[1]/'scripts/bootstrap-server.ps1'
    script=r'''
$ErrorActionPreference='Stop'
$tokens=$null; $errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile($env:BOOTSTRAP_SOURCE,[ref]$tokens,[ref]$errors)
foreach($name in @('Assert-NoReparse','Install-PythonCppRuntime')) {
 $helper=$ast.Find({param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq $name},$true)
 if(-not $helper){throw ('Missing helper '+$name)}
 Invoke-Expression $helper.Extent.Text
}
try {
 Install-PythonCppRuntime $env:CPP_TEST_ROOT
 if($env:CPP_ACCEPTED -ne 'true'){throw 'External Python directory was not rejected'}
} catch {
 if($env:CPP_ACCEPTED -eq 'false' -and $_.Exception.Message -like '*outside this installation*'){exit 0}
 throw
}
'''
    run=subprocess.run(['powershell.exe','-NoProfile','-Command',script],env={**os.environ,
        'BOOTSTRAP_SOURCE':str(source),'CPP_TEST_ROOT':str(runtime),'CPP_ACCEPTED':str(owned_base).lower()},
        capture_output=True,text=True,errors='replace',timeout=30)
    assert run.returncode==0,run.stdout+run.stderr
    if owned_base:
        for folder in (venv/'Scripts',base):
            for name in ('msvcp140.dll','vcruntime140.dll','vcruntime140_1.dll'):
                assert (folder/name).read_bytes()==(runtime/name).read_bytes()

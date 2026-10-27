import base64
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import zipfile
import io

import pytest


ROOT=Path(__file__).resolve().parents[2]


def builder():
    spec=importlib.util.spec_from_file_location('company_release',ROOT/'scripts/build-company-library-update.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def test_release_has_only_reviewed_code_and_valid_python(tmp_path):
    module=builder();target=tmp_path/'release.ps1';module.build(ROOT,target)
    source=target.read_text('utf-8-sig')
    assert 'RELEASE_PAYLOAD_' not in source
    payload=source.split("$PayloadBase64='")[1].split("'",1)[0]
    with zipfile.ZipFile(io.BytesIO(base64.b64decode(payload))) as archive:
        assert set(archive.namelist())==set(module.FILES)|{'manifest.json'}
        manifest=json.loads(archive.read('manifest.json'))
        assert set(manifest['files'])==set(module.FILES)
        for name in module.FILES:
            assert archive.read(name)==(ROOT/name).read_bytes()
            if name.endswith('.py'):compile(archive.read(name),name,'exec')
    assert 'not on the local Windows 10 PC' in source


@pytest.mark.skipif(os.name!='nt',reason='Windows PowerShell 5.1 test')
def test_powershell_extracts_and_checks_payload_and_rejects_escape(tmp_path):
    module=builder();target=tmp_path/'release.ps1';module.build(ROOT,target)
    source=target.read_text('utf-8-sig')
    # Exercise the actual delivered extraction/checksum functions without touching
    # service control or the production OS/deployment guards.
    preamble=source[:source.index('$admin=New-Object')]
    extraction=source[source.index('$bytes=[Convert]'):source.index('$script:HelperPath=')]
    stage=str(tmp_path/'stage').replace("'","''")
    script=preamble+f"\n$stage='{stage}'\n[IO.Directory]::CreateDirectory($stage)|Out-Null\n"+extraction+"""
$refused=$false
try{Child $stage '../escape.txt'|Out-Null}catch{$refused=$true}
if(-not $refused){throw 'Path escape was accepted'}
Write-Output 'POWERSHELL_51_PACKAGE_EXTRACTION_PASSED'
"""
    runner=tmp_path/'extract.ps1';runner.write_text(script,encoding='utf-8-sig')
    executable=Path(os.environ['SystemRoot'])/'System32/WindowsPowerShell/v1.0/powershell.exe'
    env=os.environ.copy();env['PSModulePath']=str(executable.parent/'Modules')+';C:\\Program Files\\WindowsPowerShell\\Modules'
    result=subprocess.run([str(executable),'-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',str(runner)],env=env,capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stdout+result.stderr
    assert 'POWERSHELL_51_PACKAGE_EXTRACTION_PASSED' in result.stdout

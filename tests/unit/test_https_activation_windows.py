"""Exercise the actual activation statement under the target PowerShell runtime."""
import base64
import os
from pathlib import Path
import subprocess

import pytest


@pytest.mark.skipif(os.name != 'nt', reason='Windows PowerShell 5.1 required')
def test_atomic_activation_preserves_previous_config_on_windows_powershell(tmp_path):
    runtime=Path(os.environ['SystemRoot'])/'System32/WindowsPowerShell/v1.0/powershell.exe'
    source=(Path(__file__).resolve().parents[2]/'scripts/enable-server-https.ps1').read_text(encoding='utf-8-sig')
    statement=next(line.strip() for line in source.splitlines() if '[IO.File]::Replace(' in line)
    directory=str(tmp_path).replace("'","''")
    script=f"""$ErrorActionPreference='Stop'
$backup='{directory}'
$candidate=Join-Path $backup 'candidate.conf'
$script:Conf=Join-Path $backup 'nginx.conf'
[IO.File]::WriteAllText($candidate,'new synthetic proxy configuration')
[IO.File]::WriteAllText($script:Conf,'original synthetic maintenance configuration')
{statement}
if([IO.File]::ReadAllText($script:Conf) -ne 'new synthetic proxy configuration'){{throw 'Replacement failed'}}
if([IO.File]::ReadAllText((Join-Path $backup 'replace-original.conf')) -ne 'original synthetic maintenance configuration'){{throw 'Original was not preserved'}}
Write-Output 'WINDOWS_ATOMIC_REPLACEMENT_OK'
"""
    encoded=base64.b64encode(script.encode('utf-16le')).decode()
    result=subprocess.run([str(runtime),'-NoProfile','-NonInteractive','-EncodedCommand',encoded],capture_output=True,text=True,timeout=20)
    assert result.returncode==0,result.stdout+result.stderr
    assert 'WINDOWS_ATOMIC_REPLACEMENT_OK' in result.stdout

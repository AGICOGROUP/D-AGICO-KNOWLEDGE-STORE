import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.skipif(sys.platform != "win32", reason="Windows PowerShell native stderr")
@pytest.mark.parametrize("native_exit", [0, 17])
def test_bootstrap_native_progress_uses_exit_status(tmp_path, native_exit):
    bootstrap = Path(__file__).resolve().parents[1] / "scripts/bootstrap-server.ps1"
    log = tmp_path / "native.log"
    child = tmp_path / "native.py"
    child.write_text("import sys\nsys.stderr.write('download progress\\n')\nsys.exit(int(sys.argv[1]))\n")
    script = r"""
$ErrorActionPreference='Stop'
$tokens=$null; $errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile($env:BOOTSTRAP_SOURCE,[ref]$tokens,[ref]$errors)
$helper=$ast.Find({param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Invoke-Checked'},$true)
Invoke-Expression $helper.Extent.Text
try {
 & { Invoke-Checked $env:NATIVE_EXE @($env:NATIVE_CHILD,$env:NATIVE_EXIT) } *> $env:NATIVE_LOG
 if($env:NATIVE_EXIT -ne '0'){throw 'Nonzero status was ignored'}
 [Console]::WriteLine('success')
} catch {
 if($env:NATIVE_EXIT -ne '0' -and $_.Exception.Message.Contains('exit '+$env:NATIVE_EXIT)){
  [Console]::WriteLine('expected failure'); exit 0
 }
 [Console]::Error.WriteLine($_.Exception.Message); exit 1
}
"""
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command", script],
        env={**os.environ, "BOOTSTRAP_SOURCE": str(bootstrap), "NATIVE_EXE": sys.executable,
             "NATIVE_EXIT": str(native_exit), "NATIVE_LOG": str(log), "NATIVE_CHILD": str(child)},
        capture_output=True, text=True, errors="replace", timeout=30, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "download progress" in log.read_text(encoding="utf-16")

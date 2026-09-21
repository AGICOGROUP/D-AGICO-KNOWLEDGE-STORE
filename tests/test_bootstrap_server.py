import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows bootstrap guards")

ROOT = Path(__file__).resolve().parents[1]


def test_bootstrap_rejects_drive_root_before_install():
    result = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(ROOT / "setup.ps1"),
            "-DataRoot",
            "D:\\",
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert result.returncode != 0
    assert "drive root" in result.stderr


def test_bootstrap_rejects_application_overlap():
    result = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(ROOT / "setup.ps1"),
            "-DataRoot",
            str(ROOT / "forbidden-data"),
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert result.returncode != 0
    assert "overlap" in result.stderr
    assert not (ROOT / "forbidden-data").exists()


def test_bootstrap_refuses_unowned_data(tmp_path):
    sentinel = tmp_path / "company-document.txt"
    sentinel.write_text("must survive")
    result = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(ROOT / "setup.ps1"),
            "-DataRoot",
            str(tmp_path),
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert result.returncode != 0
    assert "unowned DataRoot" in result.stderr
    assert sentinel.read_text() == "must survive"
    assert list(tmp_path.iterdir()) == [sentinel]


def test_chinese_marker_is_decoded_before_target_mismatch(tmp_path):
    data_root = tmp_path / "企业知识库"
    config = data_root / "config"
    config.mkdir(parents=True)
    marker = {
        "schema_version": 1,
        "app_root": str(ROOT),
        "data_root": str(data_root),
        "api_port": 18765,
        "database_port": 15432,
        "service_prefix": "AgicoKb",
        "stage": "preparing",
    }
    marker_path = config / "deployment.json"
    original = json.dumps(marker, ensure_ascii=False)
    marker_path.write_text(original, encoding="utf-8")
    result = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(ROOT / "setup.ps1"),
            "-DataRoot",
            str(data_root),
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert result.returncode != 0
    assert "Deployment target mismatch: api_port" in result.stderr
    assert marker_path.read_text(encoding="utf-8") == original


@pytest.mark.parametrize("locked_root", ["app", "data"])
def test_concurrent_setup_is_refused_without_writing(tmp_path, locked_root):
    data_root = tmp_path / "new-data"
    data_root.mkdir()
    sentinel = data_root / "existing.txt"
    sentinel.write_text("unowned data must survive")
    root = ROOT if locked_root == "app" else data_root
    digest = hashlib.sha256(str(root).lower().encode("utf-8")).hexdigest()
    name = "Global\\AgicoKbSetup_" + digest
    script = (
        "$m = New-Object Threading.Mutex($false, '" + name + "'); "
        "if (-not $m.WaitOne(0)) { exit 2 }; "
        "try { [Console]::WriteLine('ready'); [Console]::ReadLine() | Out-Null } "
        "finally { $m.ReleaseMutex(); $m.Dispose() }"
    )
    with subprocess.Popen(
        ["powershell.exe", "-NoProfile", "-Command", script],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    ) as holder:
        try:
            assert holder.stdout.readline().strip() == "ready"
            result = subprocess.run(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(ROOT / "setup.ps1"),
                    "-DataRoot",
                    str(data_root),
                ],
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            assert result.returncode != 0
            assert "Another setup is already running" in result.stderr
            assert list(data_root.iterdir()) == [sentinel]
            assert sentinel.read_text() == "unowned data must survive"
        finally:
            holder.communicate("release\n", timeout=10)


def test_initialized_resume_checks_runtime_without_reprovisioning(tmp_path):
    app_root = tmp_path / "app"
    data_root = tmp_path / "data"
    (app_root / "scripts").mkdir(parents=True)
    runtime = app_root / ".runtime"
    runtime.mkdir()
    config = data_root / "config"
    config.mkdir(parents=True)
    shutil.copyfile(ROOT / "setup.ps1", app_root / "setup.ps1")
    shutil.copyfile(
        ROOT / "scripts/bootstrap-server.ps1", app_root / "scripts/bootstrap-server.ps1"
    )
    marker = {
        "schema_version": 1,
        "app_root": str(app_root),
        "data_root": str(data_root),
        "api_port": 8765,
        "database_port": 15432,
        "service_prefix": "AgicoKb",
        "stage": "initialized",
    }
    original = json.dumps(marker)
    (runtime / "setup-target.json").write_text(original, encoding="utf-8")
    marker_path = config / "deployment.json"
    marker_path.write_text(original, encoding="utf-8")
    result = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(app_root / "setup.ps1"),
            "-DataRoot",
            str(data_root),
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert result.returncode != 0
    assert "Initialized deployment is missing runtime file" in result.stderr
    assert not (runtime / "downloads").exists()
    assert marker_path.read_text(encoding="utf-8") == original

import hashlib
import os
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from threading import Event
from types import SimpleNamespace
from uuid import uuid4

import pytest
from conftest import headers, submit

from agico_kb import operations


@pytest.mark.parametrize(
    "options",
    [
        {"timeout": float("nan")},
        {"timeout": float("inf")},
        {"stale_part_age_seconds": float("nan")},
    ],
)
def test_reconcile_invalid_limits_fail_before_database(options):
    with pytest.raises(ValueError, match="finite"):
        operations.reconcile(None, **options)


def test_reconcile_reports_integrity_and_orphans_without_modifying_files(kb):
    client, db, root = kb
    for _ in range(3):
        submit(client)
    with db.connection() as conn:
        blobs = [
            r["blob_key"] for r in conn.execute("SELECT blob_key FROM uploads ORDER BY blob_key")
        ]
    (root / blobs[0]).unlink()
    (root / blobs[1]).write_bytes(b"x" * (root / blobs[1]).stat().st_size)
    orphan, old_part, recent_part = [uuid4().hex + ext for ext in (".blob", ".part", ".part")]
    for name in (orphan, old_part, recent_part):
        (root / name).write_bytes(uuid4().bytes)
    os.utime(root / old_part, (time.time() - 90000,) * 2)
    before = {p.name: p.read_bytes() for p in root.iterdir()}
    report = operations.reconcile(client.app.state.settings)
    assert report["missing"] == [blobs[0]]
    assert report["corrupt"] == [blobs[1]]
    assert report["unreferenced_blobs"] == [orphan]
    assert report["stale_parts"] == [old_part]
    assert report["checked_references"] == 3
    assert report["stale_part_age_seconds"] == 86400
    assert {p.name: p.read_bytes() for p in root.iterdir()} == before


def test_reconcile_waits_for_upload_finalization_before_classifying_blob(kb):
    client, db, root = kb
    content = uuid4().bytes
    prepared = client.post(
        "/v1/uploads",
        headers=headers(key=uuid4().hex),
        json={"filename": "new.bin", "size": len(content)},
    ).json()
    renamed, finish = Event(), Event()
    blob = uuid4().hex + ".blob"

    def finalize():
        with db.connection(write=True) as conn:
            (root / blob).write_bytes(content)
            conn.execute(
                "UPDATE uploads SET state='complete',blob_key=%s,size=%s,sha256=%s WHERE id=%s",
                (blob, len(content), hashlib.sha256(content).hexdigest(), prepared["upload_id"]),
            )
            renamed.set()
            assert finish.wait(5)

    with ThreadPoolExecutor(2) as pool:
        writer = pool.submit(finalize)
        assert renamed.wait(5)
        scanner = pool.submit(operations.reconcile, client.app.state.settings)
        try:
            with pytest.raises(TimeoutError):
                scanner.result(timeout=0.2)
        finally:
            finish.set()
        writer.result(timeout=5)
        report = scanner.result(timeout=5)
    assert report["checked_references"] == 1
    assert not report["unreferenced_blobs"]
    assert not report["missing"] and not report["corrupt"]


def test_reconcile_timeout_releases_write_gate(kb, monkeypatch):
    client, _db, _root = kb
    submit(client)
    ticks = iter([0.0, 2.0])
    monkeypatch.setattr(
        operations, "time", SimpleNamespace(time=time.time, perf_counter=lambda: next(ticks))
    )
    with pytest.raises(TimeoutError, match="time budget"):
        operations.reconcile(client.app.state.settings, timeout=1)
    assert submit(client)[0].status_code == 201


def test_reconcile_cli_emits_json_without_credentials(kb, monkeypatch, capsys):
    import json

    client, _db, _root = kb
    submit(client)
    settings = client.app.state.settings
    monkeypatch.setenv("AGICO_KB_DATABASE_URL", settings.database_url)
    monkeypatch.setenv("AGICO_KB_STORAGE_ROOT", str(settings.storage_root))
    monkeypatch.setenv("AGICO_KB_SCHEMA", settings.schema)
    monkeypatch.setattr("sys.argv", ["operations", "reconcile"])
    assert operations.main() == 0
    output = capsys.readouterr()
    report = json.loads(output.out)
    assert report["checked_references"] == 1
    assert not report["missing"] and not report["corrupt"]
    assert settings.database_url not in output.out + output.err

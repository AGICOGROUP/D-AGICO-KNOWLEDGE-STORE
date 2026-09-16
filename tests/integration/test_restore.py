import json
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from conftest import headers, submit
from fastapi.testclient import TestClient
from psycopg import sql
from psycopg.conninfo import make_conninfo

from agico_kb import operations
from agico_kb.errors import KBError
from agico_kb.main import create_app

PG_BIN = Path(".local/postgres/Library/bin").resolve()


def test_pause_rejects_writes_but_keeps_reads_available(kb):
    client, db, _ = kb
    with operations.pause_writes(db.settings, timeout=2):
        with ThreadPoolExecutor(max_workers=12) as pool:
            responses = list(
                pool.map(
                    lambda _: client.post(
                        "/v1/uploads",
                        headers=headers(key=uuid4().hex),
                        json={"filename": "a.txt", "size": 2},
                    ),
                    range(16),
                )
            )
        assert all(
            r.status_code == 503 and r.json()["error"]["code"] == "MAINTENANCE" for r in responses
        )
        assert client.get("/v1/catalog", headers=headers()).status_code == 200
        assert client.get("/health/ready").status_code == 200
        with pytest.raises(KBError):
            db.migrate()
    assert (
        client.post(
            "/v1/uploads", headers=headers(key=uuid4().hex), json={"filename": "a.txt", "size": 2}
        ).status_code
        == 201
    )


def test_backup_waits_for_existing_writer_and_releases_on_timeout(kb):
    _, db, _ = kb
    with (
        db.connection(write=True),
        pytest.raises(TimeoutError),
        operations.pause_writes(db.settings, timeout=0.1),
    ):
        pytest.fail("Acquired before writer committed")
    with operations.pause_writes(db.settings, timeout=1):
        pass


def test_restore_new_database_and_storage_preserves_permissions(kb, tmp_path):
    client, db, root = kb
    response, _, content = submit(client, visibility="private")
    assert response.status_code == 201
    version = response.json()["version_id"]
    (root / "unreferenced.part").write_bytes(b"not committed")
    with db.connection() as conn:
        conn.execute(
            "UPDATE jobs SET state='running',generation=%s,attempts=1,lease_until=now()+interval '1 hour'",
            (uuid4(),),
        )
    archive = root.parent / ("backup_" + uuid4().hex)
    operations.backup(db.settings, archive, PG_BIN)
    manifest = json.loads((archive / "manifest.json").read_text(encoding="utf-8"))
    assert len(manifest["blobs"]) == 1
    assert db.settings.database_url not in (archive / "manifest.json").read_text(encoding="utf-8")
    target_name = "restore_test_" + uuid4().hex
    target_root = root.parent / target_name
    restored = None
    try:
        restored = operations.restore(
            archive, db.settings.database_url, target_name, target_root, PG_BIN
        )
        app = create_app(restored)
        with TestClient(app) as recovered:
            assert (
                recovered.get(f"/v1/versions/{version}/content", headers=headers()).content
                == content
            )
            assert (
                recovered.get(
                    f"/v1/versions/{version}/content", headers=headers("peer")
                ).status_code
                == 404
            )
            with app.state.db.connection() as conn:
                job = conn.execute("SELECT * FROM jobs").fetchone()
                assert (
                    job["state"] == "queued"
                    and job["generation"] is None
                    and job["lease_until"] is None
                )
        with pytest.raises(ValueError):
            operations.restore(
                archive,
                db.settings.database_url,
                target_name,
                root.parent / (target_name + "_other"),
                PG_BIN,
            )
    finally:
        if restored:
            assert target_name.startswith("restore_test_") and len(target_name) == 45
            with psycopg.connect(db.settings.database_url, autocommit=True) as conn:
                conn.execute(
                    sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(target_name))
                )


def test_backup_rejects_corrupt_original_and_never_marks_complete(kb):
    client, db, root = kb
    submit(client)
    with db.connection() as conn:
        key = conn.execute("SELECT blob_key FROM uploads").fetchone()["blob_key"]
    (root / key).write_bytes(b"corrupt")
    archive = root.parent / ("bad_" + uuid4().hex)
    with pytest.raises(ValueError):
        operations.backup(db.settings, archive, PG_BIN)
    assert not (archive / "manifest.json").exists()


@pytest.mark.parametrize("artifact", ["database.dump", "uv.lock", "original"])
def test_restore_checksum_tampering_is_rejected_before_database_creation(kb, artifact):
    client, db, root = kb
    submit(client)
    archive = root.parent / ("tamper_" + uuid4().hex)
    manifest = operations.backup(db.settings, archive, PG_BIN)
    target = (
        archive / artifact
        if artifact != "original"
        else archive / "blobs" / manifest["blobs"][0]["path"]
    )
    target.write_bytes(b"fresh intentional corruption " + uuid4().hex.encode())
    name = "restore_test_" + uuid4().hex
    with pytest.raises(ValueError):
        operations.restore(archive, db.settings.database_url, name, root.parent / name, PG_BIN)
    with db.connection() as conn:
        assert not conn.execute("SELECT 1 FROM pg_database WHERE datname=%s", (name,)).fetchone()


def test_restore_rejects_tamper_traversal_and_existing_storage_before_create(kb):
    client, db, root = kb
    submit(client)
    archive = root.parent / ("backup_" + uuid4().hex)
    operations.backup(db.settings, archive, PG_BIN)
    name = "restore_test_" + uuid4().hex
    with pytest.raises(ValueError):
        operations.restore(archive, db.settings.database_url, name, root, PG_BIN)
    manifest_file = archive / "manifest.json"
    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    manifest["blobs"][0]["path"] = "../escape"
    manifest_file.write_text(json.dumps(manifest))
    with pytest.raises(ValueError):
        operations.restore(archive, db.settings.database_url, name, root.parent / name, PG_BIN)
    with psycopg.connect(db.settings.database_url) as conn:
        assert not conn.execute("SELECT 1 FROM pg_database WHERE datname=%s", (name,)).fetchone()


def test_public_schema_restore_preserves_search_and_publication(kb, real_embedder):
    from test_search import index_text

    from agico_kb.config import Settings

    _, source_db, root = kb
    source_name = "restore_test_" + uuid4().hex
    target_name = "restore_test_" + uuid4().hex
    created = []
    try:
        with psycopg.connect(source_db.settings.database_url, autocommit=True) as conn:
            conn.execute(
                sql.SQL("CREATE DATABASE {} TEMPLATE template0").format(sql.Identifier(source_name))
            )
            created.append(source_name)
        settings = Settings(
            make_conninfo(source_db.settings.database_url, dbname=source_name),
            root / "public_source",
        )
        app = create_app(settings)
        app.state.db.migrate()
        with source_db.connection() as source, app.state.db.connection(write=True) as target:
            for table in ("principals", "memberships", "access_tokens"):
                for row in source.execute(
                    sql.SQL("SELECT * FROM {}").format(sql.Identifier(table))
                ).fetchall():
                    target.execute(
                        sql.SQL("INSERT INTO {} ({}) VALUES ({})").format(
                            sql.Identifier(table),
                            sql.SQL(",").join(map(sql.Identifier, row)),
                            sql.SQL(",").join(sql.Placeholder() for _ in row),
                        ),
                        tuple(row.values()),
                    )
        with TestClient(app) as client:
            published = index_text(
                (client, app.state.db, settings.storage_root), "恢复演练耐高温资料", real_embedder
            )
            archive = root / "public_backup"
            operations.backup(settings, archive, PG_BIN)
        # Track unique target even on partial failure for test-only cleanup.
        created.append(target_name)
        recovered_settings = operations.restore(
            archive, source_db.settings.database_url, target_name, root / "public_restored", PG_BIN
        )
        with TestClient(create_app(recovered_settings)) as recovered:
            result = recovered.post(
                "/v1/search", headers=headers("peer"), json={"query": "耐高温"}
            ).json()
            assert published["version_id"] in {r["version_id"] for r in result["items"]}
            assert (
                recovered.post(
                    "/v1/search", headers=headers("bob"), json={"query": "耐高温"}
                ).json()["items"]
                == []
            )
            with recovered.app.state.db.connection() as conn:
                assert (
                    str(
                        conn.execute("SELECT active_version_id FROM documents").fetchone()[
                            "active_version_id"
                        ]
                    )
                    == published["version_id"]
                )
    finally:
        for name in reversed(created):
            assert name.startswith("restore_test_") and len(name) == 45
            with psycopg.connect(source_db.settings.database_url, autocommit=True) as conn:
                conn.execute(
                    sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name))
                )


def test_offline_model_identity_pin_and_keyword_fallback(kb, real_embedder):
    from test_search import index_text

    from agico_kb.embeddings import LocalEmbedding, QueryEmbedding

    client, db, _ = kb
    item = index_text(kb, "离线恢复关键词", real_embedder)
    good = replace(db.settings, model_offline=True, expected_model_identity=real_embedder.identity)
    assert LocalEmbedding(good).identity == real_embedder.identity
    bad = replace(good, expected_model_identity="wrong-model-identity")
    with pytest.raises(ValueError, match="identity"):
        LocalEmbedding(bad)
    client.app.state.search.query_model.close()
    client.app.state.search.query_model = QueryEmbedding(bad)
    result = client.post("/v1/search", headers=headers("peer"), json={"query": "关键词"}).json()
    assert item["version_id"] in {r["version_id"] for r in result["items"]}
    assert result["warnings"]


def test_pause_upload_finalization_leaves_no_committed_original(kb):
    client, db, root = kb
    result = client.post(
        "/v1/uploads", headers=headers(key=uuid4().hex), json={"filename": "new.txt", "size": 3}
    )
    uid = result.json()["upload_id"]
    with operations.pause_writes(db.settings):
        result = client.put(f"/v1/uploads/{uid}/content", headers=headers(), content=b"new")
        assert result.status_code == 503
        assert not list(root.glob("*.blob")) and not list(root.glob("*.part"))
    assert (
        client.put(f"/v1/uploads/{uid}/content", headers=headers(), content=b"new").status_code
        == 200
    )


def test_lost_backup_guard_never_produces_completed_backup(kb, monkeypatch):
    from contextlib import contextmanager

    client, db, root = kb
    submit(client)
    original_pause, original_tool = operations.pause_writes, operations.pg_tool
    guard_pid = None

    @contextmanager
    def pause(*args, **kwargs):
        nonlocal guard_pid
        with original_pause(*args, **kwargs) as guard:
            guard_pid = guard.info.backend_pid
            yield guard

    def dump_then_disconnect(*args, **kwargs):
        result = original_tool(*args, **kwargs)
        with psycopg.connect(db.settings.database_url, autocommit=True) as conn:
            conn.execute("SELECT pg_terminate_backend(%s)", (guard_pid,))
        return result

    monkeypatch.setattr(operations, "pause_writes", pause)
    monkeypatch.setattr(operations, "pg_tool", dump_then_disconnect)
    archive = root.parent / ("lost_guard_" + uuid4().hex)
    with pytest.raises(psycopg.Error):
        operations.backup(db.settings, archive, PG_BIN)
    assert not (archive / "manifest.json").exists()
    with db.connection(write=True) as conn:
        assert conn.execute("SELECT 1").fetchone()


def test_readiness_database_outage_is_bounded(kb):
    import time

    _, db, _ = kb
    settings = replace(db.settings, database_url=make_conninfo(db.settings.database_url, port=1))
    with TestClient(create_app(settings)) as client:
        start = time.monotonic()
        assert client.get("/health/ready").status_code == 503
        assert time.monotonic() - start < 4
        assert client.get("/health/live").status_code == 200


def test_all_mutation_entrypoints_respect_pause(kb):
    from agico_kb import admin
    from agico_kb.worker import Worker

    client, db, _ = kb
    created, body, _ = submit(client)
    vid = created.json()["version_id"]
    did = created.json()["document_id"]
    worker = Worker(db, db.settings)
    with operations.pause_writes(db.settings):
        calls = [
            ("post", "/v1/submissions", body),
            ("post", f"/v1/versions/{vid}/publish", {"expected_revision": 0}),
            ("post", f"/v1/versions/{vid}/restore", {"expected_revision": 0}),
            ("post", f"/v1/documents/{did}/withdraw", {"expected_revision": 0}),
            ("post", f"/v1/submissions/{vid}/withdraw", None),
            (
                "put",
                f"/v1/documents/{did}/grants",
                {"expected_revision": 0, "principal_ids": ["peer"]},
            ),
            (
                "post",
                f"/v1/versions/{vid}/links",
                {"child_version_id": str(uuid4()), "label": "new"},
            ),
            ("post", f"/v1/versions/{vid}/retry", None),
        ]
        for method, url, body in calls:
            assert (
                getattr(client, method)(url, headers=headers("chief"), json=body).status_code == 503
            )
        for call in [
            lambda: admin.set_identity(db, "new", "new", "baiste", "member"),
            lambda: admin.issue_token(db, "alice", None),
            lambda: admin.revoke_tokens(db, "alice"),
            worker.claim,
            lambda: worker.retry(vid),
        ]:
            with pytest.raises(KBError):
                call()


def test_waiting_backup_stops_new_writers_before_old_writer_finishes(kb):
    import time

    _, db, _ = kb

    def take_backup_lock():
        with operations.pause_writes(db.settings, timeout=3):
            return True

    with ThreadPoolExecutor(max_workers=1) as pool:
        with db.connection(write=True):
            future = pool.submit(take_backup_lock)
            waiting = False
            deadline = time.monotonic() + 1
            while time.monotonic() < deadline:
                with db.connection() as conn:
                    waiting = bool(
                        conn.execute(
                            "SELECT 1 FROM pg_stat_activity WHERE application_name=%s AND wait_event_type='Lock'",
                            ("agico-backup:" + db.settings.schema,),
                        ).fetchone()
                    )
                if waiting:
                    break
                time.sleep(0.02)
            assert waiting, "Backup should queue for an exclusive lock"
            with pytest.raises(KBError), db.connection(write=True):
                pass
        assert future.result(timeout=2)


@pytest.mark.skipif(
    os.environ.get("AGICO_TEST_RESTART_DB") != "1",
    reason="Explicit isolated local cluster restart exercise only",
)
def test_actual_local_database_restart_reconnects_without_losing_state(kb, real_embedder):
    import subprocess

    from test_search import index_text

    client, db, _ = kb
    item = index_text(kb, "数据库重启演练原文", real_embedder)
    before = client.get(
        f"/v1/versions/{item['version_id']}/content", headers=headers("peer")
    ).content
    with db.connection() as conn:
        expected = conn.execute("SELECT count(*) AS n FROM jobs").fetchone()["n"]
    restart_script = Path("scripts/dev-db.ps1").resolve()

    def control(action):
        result = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-File",
                str(restart_script),
                action,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=30,
            check=False,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        assert result.returncode == 0, "Local development cluster control failed"

    try:
        control("stop")
        assert client.get("/health/live").status_code == 200
        assert client.get("/health/ready").status_code == 503
    finally:
        control("start")
    assert client.get("/health/ready").status_code == 200
    assert client.get("/v1/catalog", headers=headers("peer")).status_code == 200
    assert (
        client.get(f"/v1/versions/{item['version_id']}/content", headers=headers("peer")).content
        == before
    )
    assert (
        client.get(f"/v1/versions/{item['version_id']}/content", headers=headers("bob")).status_code
        == 404
    )
    assert client.post("/v1/search", headers=headers("peer"), json={"query": "重启演练"}).json()[
        "items"
    ]
    with db.connection() as conn:
        assert (
            conn.execute("SELECT count(*) AS n FROM versions WHERE state='published'").fetchone()[
                "n"
            ]
            == 1
        )
        assert conn.execute("SELECT count(*) AS n FROM jobs").fetchone()["n"] == expected

"""Opt-in, fresh-data process crash/restart and streamed 32 MiB integrity check."""

import hashlib
import json
import os
import re
import socket
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

import httpx
import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import make_conninfo

from agico_kb.config import Settings
from agico_kb.db import Database
from client.file_transfer import FileTransfer

pytestmark = pytest.mark.skipif(
    os.environ.get("AGICO_KB_RUN_PROCESS_RESTARTS") != "1",
    reason="set AGICO_KB_RUN_PROCESS_RESTARTS=1 for isolated subprocess validation",
)
ROOT = Path(__file__).resolve().parents[2]

# Test-only pause after the real transactional claim, before any parser starts.
# The replacement process runs the unmodified production worker module.
PAUSED_WORKER = """
import json
import os
import time
from pathlib import Path
import agico_kb.worker as worker

class PausedWorker(worker.Worker):
    def run_claim(self, claim):
        marker = Path(os.environ['RESTART_CLAIM_MARKER'])
        temporary = marker.with_suffix('.tmp')
        temporary.write_text(json.dumps({'generation': str(claim['generation']), 'pid': os.getpid()}))
        temporary.replace(marker)
        while True:
            time.sleep(0.1)

worker.Worker = PausedWorker
worker.main()
"""


def await_value(check, seconds=20):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        value = check()
        if value:
            return value
        time.sleep(0.05)
    pytest.fail("bounded subprocess validation wait expired")


@pytest.fixture
def restart_system(tmp_path):
    psutil = pytest.importorskip("psutil")
    schema = "restart_" + uuid4().hex
    dsn = make_conninfo(
        host="127.0.0.1",
        port=15432,
        dbname="agico_test",
        user="agico_dev",
        password=(ROOT / ".local/pg-password.txt").read_text().strip(),
    )
    with psycopg.connect(dsn, autocommit=True) as conn:
        assert conn.info.dbname == "agico_test"
        conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    db = Database(Settings(dsn, tmp_path / "storage", schema=schema))
    processes = []

    def start(args, extra_env=None):
        env = {
            **os.environ,
            "AGICO_KB_DATABASE_URL": dsn,
            "AGICO_KB_STORAGE_ROOT": str(tmp_path / "storage"),
            "AGICO_KB_SCHEMA": schema,
            "AGICO_KB_MODEL_OFFLINE": "true",
            **(extra_env or {}),
        }
        log = tmp_path / f"process-{len(processes)}.log"
        with log.open("wb") as output:
            proc = subprocess.Popen(
                [sys.executable, *args],
                cwd=ROOT,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=output,
                stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
        processes.append((proc, psutil.Process(proc.pid).create_time()))
        return proc

    def stop(proc):
        if proc.poll() is not None:
            return proc.returncode
        expected_birth = next(birth for owned, birth in processes if owned is proc)
        owner = psutil.Process(proc.pid)
        assert owner.create_time() == expected_birth, "refusing to stop an unrelated PID"
        # Windows venv Python is a launcher with an actual Python descendant.
        # Freeze each level before enumerating its children to prevent spawning
        # another parser while cleanup collects the owned process tree.
        try:
            pending, owned_tree = [owner], []
            while pending:
                member = pending.pop()
                try:
                    member.suspend()
                    owned_tree.append(member)
                    pending.extend(member.children())
                except psutil.NoSuchProcess:
                    pass
            for member in reversed(owned_tree):
                try:
                    member.kill()
                except psutil.NoSuchProcess:
                    pass
            _, alive = psutil.wait_procs(owned_tree, timeout=10)
            assert not alive, "owned subprocess tree did not stop within 10 seconds"
        except psutil.NoSuchProcess:
            pass
        return proc.wait(timeout=10)

    try:
        db.migrate()
        tokens = {role: uuid4().hex for role in ("owner", "publisher", "outsider")}
        with db.connection(write=True) as conn:
            for role, token in tokens.items():
                principal = role + "_" + uuid4().hex
                conn.execute("INSERT INTO principals(id,name) VALUES (%s,%s)", (principal, role))
                conn.execute(
                    "INSERT INTO memberships VALUES (%s,%s,%s)",
                    (
                        principal,
                        "xingyuan" if role == "outsider" else "baiste",
                        "publisher" if role == "publisher" else "member",
                    ),
                )
                conn.execute(
                    "INSERT INTO access_tokens(token_hash,principal_id) VALUES (%s,%s)",
                    (hashlib.sha256(token.encode()).hexdigest(), principal),
                )
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        base = f"http://127.0.0.1:{port}"

        def server():
            proc = start(
                [
                    "-m",
                    "uvicorn",
                    "agico_kb.main:app_factory",
                    "--factory",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(port),
                    "--no-access-log",
                ]
            )
            with httpx.Client(timeout=1) as health:

                def ready():
                    assert proc.poll() is None, "uvicorn exited before readiness"
                    try:
                        return health.get(base + "/health/ready").status_code == 200
                    except httpx.TransportError:
                        return False

                await_value(ready)
            return proc

        yield db, tokens, base, start, stop, server, psutil
    finally:
        try:
            for proc, _ in reversed(processes):
                stop(proc)
        finally:
            db.close()
            assert re.fullmatch(r"restart_[0-9a-f]{32}", schema)
            with psycopg.connect(dsn, autocommit=True) as conn:
                assert conn.info.dbname == "agico_test"
                conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def test_real_process_restarts_preserve_32mib_file_and_reclaim_job(restart_system, tmp_path):
    db, tokens, base, start, stop, server, psutil = restart_system
    started = time.monotonic()
    source = tmp_path / (uuid4().hex + ".bin")
    digest = hashlib.sha256()
    with source.open("xb") as stream:
        for _ in range(32):
            block = os.urandom(1024 * 1024)
            digest.update(block)
            stream.write(block)
    expected_hash = digest.hexdigest()
    first_api = server()
    auth = lambda role: {"Authorization": "Bearer " + tokens[role]}
    with FileTransfer(base, tokens["owner"]) as adapter, httpx.Client(base_url=base) as http:
        submitted = adapter.upload(
            source,
            {
                "organization_id": "baiste",
                "title": "fresh restart binary",
                "visibility": "department",
            },
        )
        vid, did = submitted["version_id"], submitted["document_id"]
        publish_body = {"expected_revision": 0, "accept_incomplete": True}
        published = http.post(
            f"/v1/versions/{vid}/publish", headers=auth("publisher"), json=publish_body
        )
        assert published.status_code == 200
        before = adapter.download(vid, tmp_path / "before.bin")
        assert before["sha256"] == expected_hash and before["size"] == 32 * 1024 * 1024
        assert http.get(f"/v1/versions/{vid}/content", headers=auth("outsider")).status_code == 404

        first_api_exit = stop(first_api)
        second_api = server()
        state = http.get(f"/v1/versions/{vid}", headers=auth("owner"))
        assert state.status_code == 200
        assert state.json()["state"] == "published"
        assert state.json()["publication_mode"] == "file_only"
        assert (
            http.post(
                f"/v1/versions/{vid}/publish", headers=auth("publisher"), json=publish_body
            ).status_code
            == 409
        )

        marker = tmp_path / "claimed.txt"
        first_worker = start(["-c", PAUSED_WORKER], {"RESTART_CLAIM_MARKER": str(marker)})
        await_value(marker.exists)
        claimed = json.loads(marker.read_text())
        assert claimed["pid"] in [first_worker.pid] + [
            child.pid for child in psutil.Process(first_worker.pid).children(recursive=True)
        ]
        assert not psutil.Process(claimed["pid"]).children(recursive=True)
        old_generation = claimed["generation"]
        first_worker_exit = stop(first_worker)
        with db.connection(write=True) as conn:
            old = conn.execute(
                "SELECT state,attempts,extract(epoch FROM lease_until-now()) AS lease_seconds "
                "FROM jobs WHERE version_id=%s",
                (vid,),
            ).fetchone()
            assert old["state"] == "running" and old["attempts"] == 1
            # Fault injection confined to this fresh schema and exact claimed job.
            # Do not reset its state/generation/attempts; exercise normal lease reclaim.
            changed = conn.execute(
                "UPDATE jobs SET lease_until=now()+interval '1 second' "
                "WHERE version_id=%s AND generation=%s AND state='running' RETURNING id",
                (vid, old_generation),
            ).fetchone()
            assert changed

        def lease_expired():
            with db.connection() as conn:
                return conn.execute(
                    "SELECT lease_until<now() AS expired FROM jobs WHERE version_id=%s", (vid,)
                ).fetchone()["expired"]

        await_value(lease_expired, seconds=5)
        second_worker = start(["-m", "agico_kb.worker"])

        def complete():
            assert second_worker.poll() is None, "replacement worker exited before completion"
            with db.connection() as conn:
                row = conn.execute("SELECT * FROM jobs WHERE version_id=%s", (vid,)).fetchone()
            return row if row["state"] == "complete" else None

        completed = await_value(complete, seconds=30)
        assert completed["attempts"] == 2 and str(completed["generation"]) != old_generation
        after = adapter.download(vid, tmp_path / "after.bin")
        assert after["sha256"] == expected_hash and after["size"] == before["size"]
        assert http.get(f"/v1/versions/{vid}/content", headers=auth("outsider")).status_code == 404
        state = http.get(f"/v1/versions/{vid}", headers=auth("owner")).json()
        assert state["state"] == "published" and state["processing_status"] == "stored_only"
        assert state["capabilities"] == {"file": True, "text": False, "vector": False}
        with db.connection() as conn:
            assert conn.execute("SELECT count(*) AS n FROM versions").fetchone()["n"] == 1
            doc = conn.execute("SELECT * FROM documents WHERE id=%s", (did,)).fetchone()
            assert str(doc["active_version_id"]) == vid and doc["revision"] == 1
            assert (
                conn.execute(
                    "SELECT count(*) AS n FROM audit_events WHERE action='publish'"
                ).fetchone()["n"]
                == 1
            )
        second_worker_exit = stop(second_worker)
        second_api_exit = stop(second_api)
    print(
        json.dumps(
            {
                "bytes": before["size"],
                "sha256": expected_hash,
                "api_pids": [first_api.pid, second_api.pid],
                "worker_pids": [first_worker.pid, second_worker.pid],
                "exit_codes": [
                    first_api_exit,
                    second_api_exit,
                    first_worker_exit,
                    second_worker_exit,
                ],
                "original_lease_seconds_remaining": float(old["lease_seconds"]),
                "injected_lease_seconds": 1,
                "reclaimed_attempts": completed["attempts"],
                "elapsed_seconds": round(time.monotonic() - started, 3),
            }
        )
    )

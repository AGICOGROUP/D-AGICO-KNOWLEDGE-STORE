from dataclasses import replace
from uuid import uuid4

from conftest import headers, submit

from agico_kb.worker import Worker


class UnavailableEmbedding:
    identity = "unavailable-test-model"
    dimension = 512

    def embed(self, texts):
        raise RuntimeError("injected embedding unavailable")


def test_vector_failure_keeps_text_and_retry_switches_atomically(kb):
    client, db, _ = kb
    created, _, _ = submit(client)
    vid = created.json()["version_id"]
    worker = Worker(db, client.app.state.settings, embedder=UnavailableEmbedding())
    assert worker.run_once()
    state = client.get(f"/v1/versions/{vid}", headers=headers()).json()
    assert state["processing_status"] == "partial"
    assert state["capabilities"]["text"] and not state["capabilities"]["vector"]
    with db.connection() as conn:
        old = conn.execute("SELECT active_generation FROM versions WHERE id=%s", (vid,)).fetchone()[
            "active_generation"
        ]
        assert (
            conn.execute("SELECT count(*) AS n FROM chunks WHERE version_id=%s", (vid,)).fetchone()[
                "n"
            ]
            > 0
        )
    worker.retry(vid)
    claim = worker.claim()
    with db.connection() as conn:
        assert (
            conn.execute("SELECT active_generation FROM versions WHERE id=%s", (vid,)).fetchone()[
                "active_generation"
            ]
            == old
        )
    assert worker.run_claim(claim)
    with db.connection() as conn:
        assert (
            conn.execute("SELECT active_generation FROM versions WHERE id=%s", (vid,)).fetchone()[
                "active_generation"
            ]
            != old
        )


def test_expired_lease_old_worker_cannot_write_or_restore_withdrawn(kb):
    client, db, _ = kb
    created, _, _ = submit(client)
    vid = created.json()["version_id"]
    worker = Worker(db, client.app.state.settings, embedder=UnavailableEmbedding())
    old = worker.claim()
    with db.connection() as conn:
        conn.execute(
            "UPDATE jobs SET lease_until=now()-interval '1 second' WHERE version_id=%s", (vid,)
        )
    current = worker.claim()
    assert current["generation"] != old["generation"]
    assert not worker.run_claim(old)
    assert client.post(f"/v1/submissions/{vid}/withdraw", headers=headers()).status_code == 200
    assert not worker.run_claim(current)
    with db.connection() as conn:
        assert (
            conn.execute("SELECT state FROM versions WHERE id=%s", (vid,)).fetchone()["state"]
            == "withdrawn"
        )
        assert (
            conn.execute("SELECT count(*) AS n FROM chunks WHERE version_id=%s", (vid,)).fetchone()[
                "n"
            ]
            == 0
        )


def test_parse_failure_retries_limited_and_original_survives(kb):
    client, db, _root = kb
    data = b"not a real PDF " + uuid4().hex.encode()
    r = client.post(
        "/v1/uploads",
        headers=headers(key=uuid4().hex),
        json={"filename": "broken.pdf", "size": len(data)},
    )
    uid = r.json()["upload_id"]
    client.put(f"/v1/uploads/{uid}/content", headers=headers(), content=data)
    r = client.post(
        "/v1/submissions",
        headers=headers(),
        json={"upload_id": uid, "title": "损坏文件", "organization_id": "baiste"},
    )
    vid = r.json()["version_id"]
    worker = Worker(
        db,
        replace(client.app.state.settings, worker_max_attempts=2),
        embedder=UnavailableEmbedding(),
    )
    assert worker.run_once()
    with db.connection() as conn:
        conn.execute("UPDATE jobs SET next_attempt_at=now() WHERE version_id=%s", (vid,))
    assert worker.run_once()
    assert not worker.run_once()
    assert client.get(f"/v1/versions/{vid}/content", headers=headers()).content == data
    assert (
        client.get(f"/v1/versions/{vid}", headers=headers()).json()["processing_status"] == "failed"
    )


def test_subprocess_timeout_is_recoverable(kb):
    client, db, _ = kb
    created, _, _ = submit(client)
    worker = Worker(
        db,
        replace(client.app.state.settings, parse_timeout_seconds=0.001),
        embedder=UnavailableEmbedding(),
    )
    assert worker.run_once()
    with db.connection() as conn:
        row = conn.execute(
            "SELECT state,last_error FROM jobs WHERE version_id=%s", (created.json()["version_id"],)
        ).fetchone()
        assert row["state"] == "queued" and "超时" in row["last_error"]


def test_failed_index_transaction_keeps_previous_generation(kb, real_embedder, monkeypatch):
    from test_search import index_text

    import agico_kb.worker as module

    client, db, _ = kb
    version = index_text(kb, "必须保留的正式文字。", real_embedder)
    with db.connection() as conn:
        before = conn.execute(
            "SELECT active_generation FROM versions WHERE id=%s", (version["version_id"],)
        ).fetchone()["active_generation"]
    worker = Worker(db, client.app.state.settings, embedder=real_embedder)
    worker.retry(version["version_id"])
    claim = worker.claim()
    duplicate = uuid4()
    monkeypatch.setattr(module, "uuid4", lambda: duplicate)
    assert worker.run_claim(claim)
    with db.connection() as conn:
        assert (
            conn.execute(
                "SELECT active_generation FROM versions WHERE id=%s", (version["version_id"],)
            ).fetchone()["active_generation"]
            == before
        )
        assert (
            conn.execute(
                "SELECT count(*) AS n FROM chunks WHERE generation=%s", (claim["generation"],)
            ).fetchone()["n"]
            == 0
        )
    response = client.post(
        "/v1/read", headers=headers(), json={"version_id": version["version_id"]}
    )
    assert "必须保留" in response.json()["items"][0]["text"]

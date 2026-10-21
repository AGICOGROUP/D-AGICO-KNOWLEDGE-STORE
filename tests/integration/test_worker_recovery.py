from dataclasses import replace
from io import BytesIO
from uuid import uuid4

from conftest import headers, submit
from docx import Document
from tokenizers import Tokenizer

from agico_kb.worker import Worker


class UnavailableEmbedding:
    identity = "unavailable-test-model"
    dimension = 512

    def embed(self, texts):
        raise RuntimeError("injected embedding unavailable")


def test_worker_indexes_long_word_content_without_model_truncation(kb_large, real_embedder):
    client, db, _ = kb_large
    document = Document()
    document.add_paragraph("安装要求：电源断开后检查端子。" * 100)
    buffer = BytesIO()
    document.save(buffer)
    data = buffer.getvalue()
    prepared = client.post(
        "/v1/uploads", headers=headers(key=uuid4().hex),
        json={"filename": "long.docx", "size": len(data)},
    )
    assert prepared.status_code == 201, prepared.text
    upload_id = prepared.json()["upload_id"]
    assert client.put(
        f"/v1/uploads/{upload_id}/content", headers=headers(), content=data
    ).status_code == 200
    created = client.post(
        "/v1/submissions", headers=headers(),
        json={"upload_id": upload_id, "organization_id": "baiste", "title": "长段落"},
    )
    assert created.status_code == 201
    assert Worker(db, client.app.state.settings, embedder=real_embedder).run_once()
    with db.connection() as conn:
        rows = conn.execute(
            """SELECT c.text,c.locator FROM chunks c JOIN versions v ON v.id=c.version_id
            WHERE v.id=%s AND c.generation=v.active_generation ORDER BY c.ordinal""",
            (created.json()["version_id"],),
        ).fetchall()
    counter = Tokenizer.from_str(real_embedder.tokenizer.to_str())
    counter.no_truncation()
    assert len(rows) > 1
    assert all(len(counter.encode(row["text"]).ids) <= 128 for row in rows)
    assert all(row["locator"]["parts"] == len(rows) for row in rows)
    assert "电源断开后检查端子" in rows[-1]["text"]


def test_early_file_only_publication_becomes_content_after_successful_parse(kb, real_embedder):
    client, db, _ = kb
    created, _, _ = submit(client)
    version_id = created.json()["version_id"]
    published = client.post(
        f"/v1/versions/{version_id}/publish", headers=headers("chief"),
        json={"expected_revision": 0, "accept_incomplete": True},
    )
    assert published.status_code == 200
    assert published.json()["publication_mode"] == "file_only"

    assert Worker(db, client.app.state.settings, embedder=real_embedder).run_once()
    current = client.get(f"/v1/versions/{version_id}", headers=headers()).json()
    assert current["processing_status"] == "ready"
    assert current["publication_mode"] == "content"


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


def test_visual_budget_wait_keeps_job_unreviewable_and_does_not_spend_attempt(kb, monkeypatch):
    from agico_kb.ingestion import Parsed
    from agico_kb.vision import VisionDeferred
    client, db, _ = kb
    created, _, _ = submit(client)
    worker = Worker(db, client.app.state.settings)
    claim = worker.claim()
    claim['filename'] = 'scan.pdf'
    monkeypatch.setattr(worker, '_pdf_page_count', lambda _: 1)
    monkeypatch.setattr(worker, '_parse_with_conversion', lambda _: Parsed())
    def defer(*args, **kwargs):
        raise VisionDeferred(3600, 'Waiting for visual budget')
    monkeypatch.setattr('agico_kb.vision.enrich_pdf', defer)
    assert worker.run_claim(claim)
    with db.connection() as conn:
        row = conn.execute('SELECT state,attempts,next_attempt_at>now(),lease_until FROM jobs WHERE id=%s', (claim['id'],)).fetchone()
        assert row['state'] == 'queued'
        assert row['attempts'] == 0
        assert row['lease_until'] is None
        version = conn.execute('SELECT processing_status,active_generation FROM versions WHERE id=%s', (created.json()['version_id'],)).fetchone()
        assert version['processing_status'] == 'queued'
        assert version['active_generation'] is None
    response = client.post(f"/v1/versions/{created.json()['version_id']}/publish", headers=headers('chief'), json={'expected_revision': 0})
    assert response.status_code == 409


def test_retry_clears_expired_budget_warning_and_preserves_content_warnings(kb):
    from psycopg.types.json import Jsonb
    client, db, _ = kb
    created, _, _ = submit(client)
    vid = created.json()['version_id']
    with db.connection(write=True) as conn:
        conn.execute('UPDATE versions SET warnings=%s WHERE id=%s', (Jsonb(['视觉识别每日额度已用完，任务保留并将在额度恢复后自动继续。', 'PDF 图形内容未解释。']), vid))
    Worker(db, client.app.state.settings).retry(vid)
    with db.connection() as conn:
        row = conn.execute('SELECT warnings FROM versions WHERE id=%s', (vid,)).fetchone()
        assert row['warnings'] == ['PDF 图形内容未解释。']
        job = conn.execute('SELECT state,last_error,next_attempt_at<=now() AS due FROM jobs WHERE version_id=%s', (vid,)).fetchone()
        assert job['state'] == 'queued' and job['last_error'] is None and job['due']


def test_pdf_continuation_reuses_native_checkpoint_and_explicit_retry_clears_it(kb, monkeypatch):
    from agico_kb.ingestion import Parsed
    client, db, _ = kb
    created, _, _ = submit(client)
    worker = Worker(db, client.app.state.settings)
    claim = worker.claim()
    claim['filename'] = 'scan.pdf'
    monkeypatch.setattr(worker, '_pdf_page_count', lambda _: 1)
    calls = []
    def parse(_):
        calls.append(1)
        parsed = Parsed()
        parsed.add('620 mm', kind='page', page=1)
        return parsed
    monkeypatch.setattr(worker, '_parse_with_conversion', parse)
    assert worker._pdf_base(claim).chunks[0].text == '620 mm'
    assert worker._pdf_base(claim).chunks[0].text == '620 mm'
    assert len(calls) == 1
    worker.retry(created.json()['version_id'])
    assert not worker._checkpoint_path(claim['version_id']).exists()


def test_long_pdf_uses_segmented_parse_first_and_records_real_pages(kb, monkeypatch):
    from agico_kb.ingestion import Parsed
    client, db, _ = kb
    created, _, _ = submit(client)
    worker = Worker(db, client.app.state.settings)
    claim = worker.claim()
    monkeypatch.setattr(worker, '_pdf_page_count', lambda _: 16)
    calls = []
    def segment(_, first_page=None, last_page=None):
        calls.append((first_page, last_page))
        parsed = Parsed()
        parsed.add(f'Pages {first_page}-{last_page}', kind='page', page=first_page)
        return parsed
    monkeypatch.setattr(worker, '_parse', segment)
    monkeypatch.setattr(worker, '_parse_with_conversion', lambda _: (_ for _ in ()).throw(AssertionError('Whole-document pass must not run')))
    parsed = worker._pdf_base(claim)
    assert len(parsed.chunks) == 8
    assert calls[0] == (1, 2) and calls[-1] == (15, 16)
    with db.connection() as conn:
        progress = conn.execute("SELECT capabilities->'ingestion_progress' AS p FROM versions WHERE id=%s", (created.json()['version_id'],)).fetchone()['p']
        assert progress['completed_pages'] == 14 and progress['page'] == 15
    assert worker._pdf_base(claim).chunks == parsed.chunks
    assert len(calls) == 8

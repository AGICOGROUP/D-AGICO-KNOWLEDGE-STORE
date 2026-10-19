import hashlib
import time
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

from conftest import headers, submit


def test_parallel_same_submission_deduplicates(kb):
    client, _db, _ = kb
    content = b"new concurrent synthetic file " + uuid4().hex.encode()
    r = client.post(
        "/v1/uploads",
        headers=headers(key=uuid4().hex),
        json={"filename": "test.txt", "size": len(content)},
    )
    uid = r.json()["upload_id"]
    assert (
        client.put(f"/v1/uploads/{uid}/content", headers=headers(), content=content).status_code
        == 200
    )
    body = {"upload_id": uid, "organization_id": "baiste", "title": "新文件"}
    with ThreadPoolExecutor(2) as pool:
        results = list(
            pool.map(
                lambda _: client.post("/v1/submissions", headers=headers(), json=body), range(2)
            )
        )
    assert sorted(r.status_code for r in results) == [200, 201]
    assert len({r.json()["version_id"] for r in results}) == 1


def test_parallel_distinct_uploads_of_identical_bytes_create_one_parse_job(kb, monkeypatch):
    from agico_kb import files

    client, db, _ = kb
    content = b"parallel duplicate " + uuid4().hex.encode()
    uploads = []
    for _ in range(2):
        prepared = client.post(
            "/v1/uploads",
            headers=headers(key=uuid4().hex),
            json={"filename": "same.txt", "size": len(content)},
        )
        assert prepared.status_code == 201
        upload_id = prepared.json()["upload_id"]
        assert client.put(
            f"/v1/uploads/{upload_id}/content", headers=headers(), content=content
        ).status_code == 200
        uploads.append(upload_id)

    original_lookup = files.duplicate_version

    def widened_race(conn, checksum, exclude_upload_id=None):
        found = original_lookup(conn, checksum, exclude_upload_id)
        if exclude_upload_id is not None and found is None:
            time.sleep(0.1)
        return found

    monkeypatch.setattr(files, "duplicate_version", widened_race)

    def submit_upload(upload_id):
        return client.post(
            "/v1/submissions", headers=headers(),
            json={"upload_id": upload_id, "organization_id": "baiste", "title": "相同原件"},
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(submit_upload, uploads))

    assert sorted(result.status_code for result in results) == [200, 201]
    assert sorted(result.json().get("skipped", False) for result in results) == [False, True]
    with db.connection() as conn:
        assert conn.execute("SELECT count(*) AS n FROM versions").fetchone()["n"] == 1
        assert conn.execute("SELECT count(*) AS n FROM jobs").fetchone()["n"] == 1
        assert conn.execute("SELECT count(*) AS n FROM uploads WHERE sha256=%s", (hashlib.sha256(content).hexdigest(),)).fetchone()["n"] == 2


def test_expired_upload_and_replacement_content_immutable(kb):
    client, db, _ = kb
    response, body, content = submit(client)
    uid = body["upload_id"]
    assert (
        client.put(
            f"/v1/uploads/{uid}/content", headers=headers(), content=b"X" * len(content)
        ).status_code
        == 409
    )
    version = response.json()["version_id"]
    assert client.get(f"/v1/versions/{version}/content", headers=headers()).content == content
    prepared = client.post(
        "/v1/uploads", headers=headers(key=uuid4().hex), json={"filename": "x", "size": 1}
    )
    uid = prepared.json()["upload_id"]
    with db.connection() as conn:
        conn.execute("UPDATE uploads SET expires_at=now()-interval '1 second' WHERE id=%s", (uid,))
    assert (
        client.put(f"/v1/uploads/{uid}/content", headers=headers(), content=b"x").status_code == 409
    )


def test_metadata_roundtrip_allows_another_employee_to_submit_revision(kb):
    client, _, _ = kb
    first, _, _ = submit(
        client,
        product="传动设备",
        model="AX-210",
        project="示例项目",
        business_date="2026-08-01",
        source_version="R1",
    )
    version = first.json()["version_id"]
    assert (
        client.post(
            f"/v1/versions/{version}/publish",
            headers=headers("chief"),
            json={"expected_revision": 0, "accept_incomplete": True},
        ).status_code
        == 200
    )
    metadata = client.get(f"/v1/versions/{version}", headers=headers("peer")).json()
    keys = [
        "document_id",
        "organization_id",
        "category_id",
        "title",
        "visibility",
        "product",
        "model",
        "project",
        "business_date",
    ]
    body = {k: metadata[k] for k in keys}
    revised, _, _ = submit(client, person="peer", base_revision=metadata["revision"], **body)
    assert revised.status_code == 201


def test_stale_submission_retry_preserves_conflict(kb):
    client, _, _ = kb
    first, _, _ = submit(client)
    version = first.json()["version_id"]
    client.post(
        f"/v1/versions/{version}/publish",
        headers=headers("chief"),
        json={"expected_revision": 0, "accept_incomplete": True},
    )
    stale, body, _ = submit(client, document_id=first.json()["document_id"], base_revision=0)
    assert stale.status_code == 409
    repeated = client.post("/v1/submissions", headers=headers(), json=body)
    assert repeated.status_code == 409
    assert repeated.json()["version_id"] == stale.json()["version_id"]

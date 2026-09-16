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

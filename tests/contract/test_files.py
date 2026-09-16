import hashlib
from uuid import uuid4

from conftest import headers, submit


def test_upload_submit_download_and_repeat(kb):
    client, db, _root = kb
    response, body, content = submit(client)
    assert response.status_code == 201, response.text
    version = response.json()["version_id"]
    assert response.json()["processing_status"] == "queued"
    repeat = client.post("/v1/submissions", headers=headers(), json=body)
    assert repeat.json()["version_id"] == version
    download = client.get(f"/v1/versions/{version}/content", headers=headers())
    assert download.content == content
    assert hashlib.sha256(download.content).hexdigest() == response.json()["sha256"]
    for person in ["peer", "bob", "otherchief"]:
        assert client.get(f"/v1/versions/{version}", headers=headers(person)).status_code == 404
        assert (
            client.get(f"/v1/versions/{version}/content", headers=headers(person)).status_code
            == 404
        )
    with db.connection() as conn:
        assert conn.execute("SELECT count(*) AS n FROM versions").fetchone()["n"] == 1
        assert conn.execute("SELECT count(*) AS n FROM jobs").fetchone()["n"] == 1


def test_incomplete_hash_limit_and_idempotency(kb):
    client, _db, root = kb
    body = {
        "filename": "中文资料.txt",
        "size": 10,
        "sha256": hashlib.sha256(b"0123456789").hexdigest(),
    }
    key = uuid4().hex
    prepared = client.post("/v1/uploads", headers=headers(key=key), json=body)
    assert prepared.status_code == 201
    uid = prepared.json()["upload_id"]
    assert (
        client.post("/v1/uploads", headers=headers(key=key), json=body).json()["upload_id"] == uid
    )
    assert (
        client.post("/v1/uploads", headers=headers(key=key), json={**body, "size": 9}).status_code
        == 409
    )
    for content in [b"012", b"01234567890", b"wrong-hash"]:
        assert (
            client.put(f"/v1/uploads/{uid}/content", headers=headers(), content=content).status_code
            == 422
        )
    assert not list(root.rglob("*.part"))
    assert (
        client.post(
            "/v1/submissions",
            headers=headers(),
            json={"upload_id": uid, "organization_id": "baiste", "title": "文件"},
        ).status_code
        == 409
    )
    assert (
        client.put(
            f"/v1/uploads/{uid}/content", headers=headers("bob"), content=b"0123456789"
        ).status_code
        == 404
    )
    assert (
        client.put(
            f"/v1/uploads/{uid}/content", headers=headers(), content=b"0123456789"
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/v1/uploads",
            headers=headers(key=uuid4().hex),
            json={"filename": "large", "size": 1025},
        ).status_code
        == 422
    )


def test_identity_and_catalog(kb):
    client, db, _ = kb
    response = client.get("/v1/catalog", headers=headers())
    assert response.status_code == 200
    assert [x["id"] for x in response.json()["organizations"]] == ["baiste"]
    assert len(response.json()["categories"]) == 6
    response, _, _ = submit(client, organization_id="xingyuan")
    assert response.status_code == 404
    response, _, _ = submit(client, role="publisher")
    assert response.status_code == 422
    with db.connection() as conn:
        conn.execute("UPDATE access_tokens SET revoked=true WHERE principal_id='alice'")
    assert client.get("/v1/catalog", headers=headers()).status_code == 401


def test_filename_path_and_changed_submission_rejected(kb):
    client, _, _ = kb
    for name in ["../secret", "C:\\file", "/absolute", "..", "bad\x00name"]:
        assert (
            client.post(
                "/v1/uploads", headers=headers(key=uuid4().hex), json={"filename": name, "size": 3}
            ).status_code
            == 422
        )
    response, body, _ = submit(client)
    assert response.status_code == 201
    assert (
        client.post(
            "/v1/submissions", headers=headers(), json={**body, "title": "changed"}
        ).status_code
        == 409
    )

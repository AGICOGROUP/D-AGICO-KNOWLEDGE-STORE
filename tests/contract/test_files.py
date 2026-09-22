import hashlib
from pathlib import Path
from uuid import uuid4

import pytest
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


def test_identical_content_is_skipped_as_duplicate(kb):
    client, db, _root = kb
    content = b"identical-bytes-dedupe-check"
    digest = hashlib.sha256(content).hexdigest()
    body = {
        "organization_id": "baiste",
        "category_id": "technical",
        "title": "重复资料",
        "visibility": "department",
        "base_revision": 0,
    }

    def prepare(person="alice", declared=True):
        payload = {"filename": "重复资料.md", "size": len(content)}
        if declared:
            payload["sha256"] = digest
        return client.post("/v1/uploads", headers=headers(person, uuid4().hex), json=payload)

    first = prepare()
    assert first.status_code == 201
    upload_id = first.json()["upload_id"]
    assert (
        client.put(
            f"/v1/uploads/{upload_id}/content", headers=headers(), content=content
        ).status_code
        == 200
    )
    created = client.post(
        "/v1/submissions", headers=headers(), json={**body, "upload_id": upload_id}
    )
    assert created.status_code == 201, created.text
    version = created.json()["version_id"]

    # A declared hash is rejected before any bytes are transferred.
    early = prepare()
    assert early.status_code == 200
    assert early.json()["skipped"] is True
    assert early.json()["duplicate_of"]["title"] == "重复资料"
    assert "upload_id" not in early.json()

    # A client that declares no hash is still caught by the server-computed hash.
    silent = prepare(declared=False)
    assert silent.status_code == 201
    silent_id = silent.json()["upload_id"]
    assert (
        client.put(
            f"/v1/uploads/{silent_id}/content", headers=headers(), content=content
        ).status_code
        == 200
    )
    skipped = client.post(
        "/v1/submissions", headers=headers(), json={**body, "upload_id": silent_id}
    )
    assert skipped.status_code == 200
    assert skipped.json()["skipped"] is True
    with db.connection() as conn:
        assert conn.execute("SELECT count(*) AS n FROM versions").fetchone()["n"] == 1

    # A withdrawn file is no longer considered a duplicate, so it can be uploaded again.
    assert client.post(f"/v1/submissions/{version}/withdraw", headers=headers()).status_code == 200
    again = prepare()
    assert again.status_code == 201, again.text


def test_lock_file_names_are_rejected(kb):
    client, _, _ = kb
    for name in ["~$初始填充材料清单.docx", ".~设计文档.xlsx"]:
        prepared = client.post(
            "/v1/uploads",
            headers=headers(key=uuid4().hex),
            json={"filename": name, "size": 10},
        )
        assert prepared.status_code == 422, prepared.text
        assert "临时锁文件" in prepared.json()["error"]["message"]
    # A normal name with a tilde elsewhere is fine.
    ok = client.post(
        "/v1/uploads",
        headers=headers(key=uuid4().hex),
        json={"filename": "价格~清单.docx", "size": 10},
    )
    assert ok.status_code == 201


def test_xls_fallback_recovers_values(kb):
    """With LibreOffice unavailable, .xls still parses via the pure-Python fallback."""
    import xlrd  # noqa: F401 - proves the dependency is importable in the test venv

    from agico_kb.convert import CONVERTIBLE
    from agico_kb.fallback_parse import _xls_fallback
    from agico_kb.ingestion import Parsed, parse_file

    _client, _db, root = kb
    xls_path = root / "报价.xls"
    import subprocess

    soffice = None
    for candidate in [
        Path("C:/Program Files/LibreOffice/program/soffice.exe"),
        Path("C:/Program Files (x86)/LibreOffice/program/soffice.exe"),
    ]:
        if candidate.exists():
            soffice = candidate
            break
    if soffice:
        xlsx = root / "报价.xlsx"
        sheet = [["客户", "项目", "金额"], ["XX水泥厂", "石灰产线", "1200000"]]
        from openpyxl import Workbook

        wb = Workbook()
        ws = wb.active
        for row in sheet:
            ws.append(row)
        wb.save(xlsx)
        subprocess.run(
            [
                str(soffice),
                "--headless",
                "--norestore",
                "--convert-to",
                "xls",
                "--outdir",
                str(root),
                str(xlsx),
            ],
            capture_output=True,
            timeout=180,
            creationflags=subprocess.CREATE_NO_WINDOW,
            check=False,
        )
    if not xls_path.exists():
        pytest.skip("cannot fabricate a real .xls without LibreOffice")
    result = Parsed()
    _xls_fallback(xls_path, result)
    assert result.chunks, "fallback must recover rows"
    assert any("XX水泥厂" in c.text for c in result.chunks)
    assert result.warnings and "兜底" in result.warnings[0]
    # The ingestion entry point must not treat the parsed text as a different format.
    direct = parse_file(xls_path, "报价.xls")
    assert direct.status in {"ready", "partial"}
    assert CONVERTIBLE[".xls"] == ".xlsx"


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

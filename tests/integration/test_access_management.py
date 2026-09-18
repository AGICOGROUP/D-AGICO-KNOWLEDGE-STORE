from uuid import uuid4

from conftest import headers


def owner(db):
    with db.connection(write=True) as conn:
        conn.execute("UPDATE principals SET is_admin=true WHERE id='chief'")


def payload(**changes):
    return {
        "request_id": str(uuid4()),
        "name": "外地测试",
        "organizations": ["baiste"],
        "days": 30,
        **changes,
    }


def test_access_management_requires_explicit_admin(kb):
    client, db, _ = kb
    assert client.get("/v1/admin/access").status_code == 401
    for who in ["alice", "chief"]:
        assert client.get("/v1/admin/access", headers=headers(who)).status_code == 403
        assert (
            client.post("/v1/admin/access", headers=headers(who), json=payload()).status_code == 403
        )
    owner(db)
    assert client.get("/v1/admin/session", headers=headers("chief")).status_code == 200


def test_issue_scope_list_secret_once_and_revoke(kb):
    client, db, _ = kb
    owner(db)
    body = payload()
    response = client.post("/v1/admin/access", headers=headers("chief"), json=body)
    assert response.status_code == 201, response.text
    assert response.headers["cache-control"] == "no-store"
    issued = response.json()
    token = issued["token"]
    auth = {"Authorization": "Bearer " + token}
    catalog = client.get("/v1/catalog", headers=auth).json()
    assert [o["id"] for o in catalog["organizations"]] == ["baiste"]
    assert client.get("/v1/admin/access", headers=auth).status_code == 403
    with db.connection() as conn:
        roles = conn.execute(
            "SELECT role FROM memberships WHERE principal_id=%s", (issued["principal_id"],)
        ).fetchall()
        assert [r["role"] for r in roles] == ["member"]
    listing = client.get("/v1/admin/access", headers=headers("chief"))
    assert token not in listing.text and "token_hash" not in listing.text
    assert listing.json()["items"][0]["status"] == "active"
    repeat = client.post("/v1/admin/access", headers=headers("chief"), json=body)
    assert repeat.status_code == 409
    assert len(client.get("/v1/admin/access", headers=headers("chief")).json()["items"]) == 1
    path = "/v1/admin/access/" + issued["id"] + "/revoke"
    assert client.post(path, headers=headers("alice")).status_code == 403
    assert client.post(path, headers=headers("chief")).status_code == 200
    assert client.get("/v1/catalog", headers=auth).status_code == 401
    assert (
        client.get("/v1/admin/access", headers=headers("chief")).json()["items"][0]["status"]
        == "revoked"
    )
    assert client.post(path, headers=headers("chief")).status_code == 200


def test_access_validation_and_pagination(kb):
    client, db, _ = kb
    owner(db)
    for changes in [
        {"organizations": []},
        {"organizations": ["missing"]},
        {"days": 0},
        {"days": 366},
        {"name": "  "},
        {"is_admin": True},
    ]:
        assert (
            client.post(
                "/v1/admin/access", headers=headers("chief"), json=payload(**changes)
            ).status_code
            == 422
        )
    for i in range(3):
        assert (
            client.post(
                "/v1/admin/access", headers=headers("chief"), json=payload(name=str(i))
            ).status_code
            == 201
        )
    first = client.get("/v1/admin/access?limit=2", headers=headers("chief")).json()
    second = client.get("/v1/admin/access?limit=2&offset=2", headers=headers("chief")).json()
    assert len(first["items"]) == 2 and first["has_more"]
    assert len(second["items"]) == 1 and not second["has_more"]
    assert (
        client.post(
            "/v1/admin/access/" + str(uuid4()) + "/revoke", headers=headers("chief")
        ).status_code
        == 404
    )


def test_batch_issue_unique_scoped_credentials_and_retry(kb):
    client, db, _ = kb
    owner(db)
    body = payload(count=10)
    response = client.post("/v1/admin/access", headers=headers("chief"), json=body)
    assert response.status_code == 201, response.text
    items = response.json()["items"]
    assert len(items) == 10 and len({item["token"] for item in items}) == 10
    assert len({item["principal_id"] for item in items}) == 10
    assert items[0]["name"] == "外地测试 #01" and items[-1]["name"] == "外地测试 #10"
    for item in items:
        auth = {"Authorization": "Bearer " + item["token"]}
        assert [
            o["id"] for o in client.get("/v1/catalog", headers=auth).json()["organizations"]
        ] == ["baiste"]
        assert (
            client.post("/v1/admin/access", headers=auth, json=payload(count=10)).status_code == 403
        )
    assert (
        client.post(
            "/v1/admin/access", headers=headers("chief"), json={**body, "count": 15}
        ).status_code
        == 409
    )
    listing = client.get("/v1/admin/access", headers=headers("chief")).json()["items"]
    assert len(listing) == 10
    assert all("token" not in item and "token_hash" not in item for item in listing)
    assert (
        client.post(
            "/v1/admin/access/" + items[0]["id"] + "/revoke", headers=headers("chief")
        ).status_code
        == 200
    )
    assert (
        client.get(
            "/v1/catalog", headers={"Authorization": "Bearer " + items[0]["token"]}
        ).status_code
        == 401
    )
    assert (
        client.get(
            "/v1/catalog", headers={"Authorization": "Bearer " + items[1]["token"]}
        ).status_code
        == 200
    )
    for count in [0, 101, 1.5]:
        assert (
            client.post(
                "/v1/admin/access", headers=headers("chief"), json=payload(count=count)
            ).status_code
            == 422
        )


def test_batch_failure_rolls_back_all_credentials(kb, monkeypatch):
    import pytest

    from agico_kb import access_management

    client, db, _ = kb
    owner(db)
    original = access_management.secrets.token_urlsafe
    calls = 0

    def fail_midway(size):
        nonlocal calls
        calls += 1
        if calls == 5:
            raise RuntimeError("synthetic generation failure")
        return original(size)

    monkeypatch.setattr(access_management.secrets, "token_urlsafe", fail_midway)
    with pytest.raises(RuntimeError, match="synthetic generation failure"):
        client.post("/v1/admin/access", headers=headers("chief"), json=payload(count=10))
    with db.connection() as conn:
        assert conn.execute("SELECT count(*) AS n FROM managed_access").fetchone()["n"] == 0
        assert (
            conn.execute(
                "SELECT count(*) AS n FROM principals WHERE id LIKE 'shared-%'"
            ).fetchone()["n"]
            == 0
        )

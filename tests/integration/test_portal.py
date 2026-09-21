from conftest import headers


def test_portal_public_shell_does_not_bypass_api_auth(kb):
    client, _, _ = kb
    page = client.get("/")
    assert page.status_code == 200
    assert "选择所属事业部" in page.text
    assert "frame-ancestors 'none'" in page.headers["content-security-policy"]
    assert client.get("/portal/app.js").status_code == 200
    assert client.get("/portal/styles.css").status_code == 200
    assert client.get("/v1/catalog").status_code == 401
    assert client.post("/mcp", json={}).status_code == 401
    assert client.get("/portal/../config.py").status_code == 404
    assert client.get("/portal/index.html").status_code == 404


def test_portal_session_metadata_scopes_organizations(kb):
    client, _, _ = kb
    assert client.get("/v1/portal-session").status_code == 401
    session = client.get("/v1/portal-session", headers=headers()).json()
    assert session == {"identity": "alice", "max_upload_bytes": 1024, "publisher_organizations": []}
    # The approval actions are gated on the units this account approves for.
    approver = client.get("/v1/portal-session", headers=headers("chief")).json()
    assert approver["publisher_organizations"] == ["baiste"]
    assert client.get("/v1/portal-session", headers=headers("otherchief")).json()[
        "publisher_organizations"
    ] == ["xingyuan"]
    catalog = client.get("/v1/catalog", headers=headers()).json()
    assert [o["id"] for o in catalog["organizations"]] == ["baiste"]
    # Missing division remains a validation error, never a default assignment.
    response = client.post(
        "/v1/submissions",
        headers=headers(),
        json={"upload_id": "00000000-0000-0000-0000-000000000000", "title": "fresh sample"},
    )
    assert response.status_code == 422

from conftest import headers, submit


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
    assert session == {
        "identity": "alice",
        "max_upload_bytes": 1024,
        "publisher_organizations": [],
        "is_admin": False,
        "failure_log": session["failure_log"],
    }
    assert session["failure_log"].endswith("failures.log")
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


def test_pending_total_counts_all_pages_with_same_visibility_scope(kb):
    client, _, _ = kb
    own, _, _ = submit(client)
    for index in range(21):
        assert submit(client, person="peer", title=f"Fresh pending {index}")[0].status_code == 201
    page = client.get("/v1/submissions?limit=20", headers=headers("chief")).json()
    assert page["total"] == 22
    assert len(page["items"]) == 20
    assert page["has_more"]
    assert client.get("/v1/submissions?limit=1", headers=headers("alice")).json()["total"] == 1
    assert client.get("/v1/submissions", headers=headers("otherchief")).json()["total"] == 0
    assert client.post(
        f"/v1/submissions/{own.json()['version_id']}/withdraw", headers=headers("alice")
    ).status_code == 200
    assert client.get("/v1/submissions?limit=1", headers=headers("chief")).json()["total"] == 21


def test_pending_list_exposes_uploaded_filename_beside_display_title(kb):
    client, _, _ = kb
    created, _, _ = submit(client, title="规范显示名称.md")
    version_id = created.json()["version_id"]
    items = client.get("/v1/submissions", headers=headers("chief")).json()["items"]
    item = next(row for row in items if row["version_id"] == version_id)
    assert item["title"] == "规范显示名称.md"
    assert item["filename"] == "公司资料.md"


def test_review_notification_counts_only_finished_actionable_drafts(kb):
    client, db, _ = kb
    created, _, _ = submit(client)
    version = created.json()["version_id"]

    def count(person="chief"):
        return client.get("/v1/submissions?limit=1", headers=headers(person)).json()["reviewable_total"]

    for status, job, expected in [
        ("queued", "queued", 0), ("processing", "running", 0), ("failed", "failed", 0),
        ("partial", "queued", 0), ("ready", "running", 0),
        ("partial", "complete", 1), ("stored_only", "complete", 1), ("ready", "complete", 1),
    ]:
        # Synthetic state fixtures exercise the notification boundary, not parsing accuracy.
        with db.connection(write=True) as conn:
            conn.execute("UPDATE versions SET processing_status=%s WHERE id=%s", (status, version))
            conn.execute("UPDATE jobs SET state=%s WHERE version_id=%s", (job, version))
        assert count() == expected, (status, job)
        assert count("alice") == 0  # Author has no approval button.
        assert count("otherchief") == 0
    assert client.post(
        f"/v1/versions/{version}/publish", headers=headers("chief"),
        json={"expected_revision": 0},
    ).status_code == 200
    assert count() == 0


def test_pending_reminder_includes_unprocessed_name_reviews(kb):
    client, db, _ = kb
    created, _, _ = submit(client)
    version_id = created.json()["version_id"]
    with db.connection(write=True) as conn:
        conn.execute(
            """UPDATE versions SET processing_status='queued',
            capabilities=jsonb_set(COALESCE(capabilities, '{}'::jsonb),
            '{naming_review}', '{"state":"review","needs_review":true}'::jsonb)
            WHERE id=%s""",
            (version_id,),
        )
    response = client.get("/v1/submissions?limit=1", headers=headers("chief")).json()
    assert response["total"] == 1
    assert response["reviewable_total"] == 0

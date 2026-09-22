from concurrent.futures import ThreadPoolExecutor

from conftest import headers, submit


def publish(client, version, revision, person="chief", accept=True):
    return client.post(
        f"/v1/versions/{version}/publish",
        headers=headers(person),
        json={"expected_revision": revision, "accept_incomplete": accept},
    )


def test_publish_permissions_and_incomplete_disclosure(kb):
    client, _, _ = kb
    result, _, _ = submit(client)
    version = result.json()["version_id"]
    for person in ["alice", "bob", "otherchief"]:
        assert publish(client, version, 0, person).status_code == 404
    assert (
        publish(client, version, 0, accept=False).json()["error"]["code"] == "PROCESSING_INCOMPLETE"
    )
    response = publish(client, version, 0)
    assert response.status_code == 200, response.text
    assert response.json()["publication_mode"] == "file_only"
    assert response.json()["processing_status"] == "queued"
    assert response.json()["revision"] == 1
    assert client.get(f"/v1/versions/{version}/content", headers=headers("peer")).status_code == 200
    assert client.get("/v1/documents", headers=headers("bob")).json()["items"] == []


def test_concurrent_publish_stale_draft_withdraw_restore(kb):
    client, _db, _ = kb
    first, _, _ = submit(client)
    v1, document = first.json()["version_id"], first.json()["document_id"]
    assert publish(client, v1, 0).status_code == 200
    a, _, _ = submit(client, document_id=document, base_revision=1)
    b, _, _ = submit(client, document_id=document, base_revision=1)
    versions = [a.json()["version_id"], b.json()["version_id"]]
    with ThreadPoolExecutor(2) as pool:
        responses = list(pool.map(lambda v: publish(client, v, 1), versions))
    assert sorted(r.status_code for r in responses) == [200, 409]
    winner = versions[[r.status_code for r in responses].index(200)]
    loser = versions[[r.status_code for r in responses].index(409)]
    assert publish(client, loser, 2).json()["error"]["code"] == "VERSION_CONFLICT"
    assert client.get(f"/v1/versions/{loser}", headers=headers()).json()["state"] == "draft"
    assert client.get("/v1/documents", headers=headers()).json()["items"][0]["version_id"] == winner
    assert client.get(f"/v1/versions/{v1}/content", headers=headers()).status_code == 404
    assert (
        client.get(f"/v1/versions/{v1}/content?history=true", headers=headers("chief")).status_code
        == 200
    )
    assert (
        client.get(f"/v1/versions/{v1}/content?history=true", headers=headers("peer")).status_code
        == 404
    )
    withdraw = client.post(
        f"/v1/documents/{document}/withdraw",
        headers=headers("chief"),
        json={"expected_revision": 2},
    )
    assert withdraw.status_code == 200
    assert client.get("/v1/documents", headers=headers()).json()["items"] == []
    assert client.get(f"/v1/versions/{winner}/content", headers=headers()).status_code == 404
    restored = client.post(
        f"/v1/versions/{v1}/restore", headers=headers("chief"), json={"expected_revision": 3}
    )
    assert restored.status_code == 200
    assert restored.json()["revision"] == 4
    assert client.get("/v1/documents", headers=headers()).json()["items"][0]["version_id"] == v1
    events = client.get(f"/v1/documents/{document}/audit", headers=headers("chief")).json()["items"]
    assert [e["action"] for e in events].count("publish") == 2
    assert events[-1]["action"] == "restore"


def test_stale_submission_preserved_and_draft_withdrawal(kb):
    client, db, _ = kb
    first, _, _ = submit(client)
    document = first.json()["document_id"]
    assert publish(client, first.json()["version_id"], 0).status_code == 200
    stale, _body, _ = submit(client, document_id=document, base_revision=0)
    assert stale.status_code == 409
    version = stale.json()["version_id"]
    assert publish(client, version, 1).status_code == 409
    assert (
        client.post(
            f"/v1/versions/{version}/restore",
            headers=headers("chief"),
            json={"expected_revision": 1},
        ).status_code
        == 409
    )
    pending = client.get("/v1/submissions", headers=headers("chief")).json()["items"]
    assert version in [p["version_id"] for p in pending]
    assert client.get("/v1/submissions", headers=headers("otherchief")).json()["items"] == []
    assert (
        client.post(f"/v1/submissions/{version}/withdraw", headers=headers("peer")).status_code
        == 404
    )
    assert client.post(f"/v1/submissions/{version}/withdraw", headers=headers()).status_code == 200
    with db.connection() as conn:
        assert (
            conn.execute("SELECT state FROM jobs WHERE version_id=%s", (version,)).fetchone()[
                "state"
            ]
            == "cancelled"
        )


def test_shared_file_permissions_change_immediately(kb):
    client, db, _ = kb
    response, _, _ = submit(client, visibility="private")
    document, version = response.json()["document_id"], response.json()["version_id"]
    assert publish(client, version, 0).status_code == 200
    assert client.get(f"/v1/versions/{version}", headers=headers("peer")).status_code == 404
    response = client.put(
        f"/v1/documents/{document}/grants",
        headers=headers("chief"),
        json={"expected_revision": 1, "principal_ids": ["bob"]},
    )
    assert response.status_code == 200
    assert client.get(f"/v1/versions/{version}/content", headers=headers("bob")).status_code == 200
    assert len(client.get("/v1/documents", headers=headers("bob")).json()["items"]) == 1
    assert (
        client.put(
            f"/v1/documents/{document}/grants",
            headers=headers("chief"),
            json={"expected_revision": 2, "principal_ids": []},
        ).status_code
        == 200
    )
    assert client.get(f"/v1/versions/{version}/content", headers=headers("bob")).status_code == 404
    with db.connection() as conn:
        assert conn.execute("SELECT count(*) AS n FROM uploads").fetchone()["n"] == 1


def test_unit_approver_rejects_another_authors_draft(kb):
    client, db, _ = kb
    created, _, _ = submit(client, person="peer")  # peer uploads, chief approves
    version = created.json()["version_id"]
    # The approval queue lists the newest submission first, so a fresh upload is not buried.
    queue = client.get("/v1/submissions", headers=headers("chief")).json()["items"]
    assert queue and queue[0]["version_id"] == version
    # Another unit's approver and the author's own unit members must not touch this draft.
    assert (
        client.post(
            f"/v1/submissions/{version}/withdraw", headers=headers("otherchief")
        ).status_code
        == 404
    )
    assert (
        client.post(f"/v1/submissions/{version}/withdraw", headers=headers("alice")).status_code
        == 404
    )
    rejected = client.post(f"/v1/submissions/{version}/withdraw", headers=headers("chief"))
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["rejected"] is True
    assert rejected.json()["state"] == "withdrawn"
    with db.connection() as conn:
        assert (
            conn.execute("SELECT state FROM versions WHERE id=%s", (version,)).fetchone()["state"]
            == "withdrawn"
        )
        latest = conn.execute(
            "SELECT action,actor_id FROM audit_events WHERE version_id=%s ORDER BY created_at DESC,id DESC LIMIT 1",
            (version,),
        ).fetchone()
        assert latest["action"] == "reject" and latest["actor_id"] == "chief"
    # A rejected submission no longer appears in the unit's approval queue.
    assert all(
        item["version_id"] != version
        for item in client.get("/v1/submissions", headers=headers("chief")).json()["items"]
    )
    # An author withdrawing their own draft stays a plain withdrawal, not a rejection.
    own, _, _ = submit(client, person="peer")
    own_version = own.json()["version_id"]
    assert (
        client.post(f"/v1/submissions/{own_version}/withdraw", headers=headers("peer")).status_code
        == 200
    )
    with db.connection() as conn:
        action = conn.execute(
            "SELECT action FROM audit_events WHERE version_id=%s ORDER BY created_at DESC,id DESC LIMIT 1",
            (own_version,),
        ).fetchone()
        assert action["action"] == "withdraw_submission"


def test_delete_document_permission_quarantine_and_audit(kb):
    """Approver deletes a published doc; other units and members cannot; blob quarantined."""

    client, db, root = kb
    created, _, _ = submit(client)
    version = created.json()["version_id"]
    document = created.json()["document_id"]
    assert (
        client.post(
            f"/v1/versions/{version}/publish",
            headers=headers("chief"),
            json={"expected_revision": 0, "accept_incomplete": True},
        ).status_code
        == 200
    )
    storage_files = list(root.rglob("*.blob")) + list(root.rglob("*.md"))

    # Cross-unit approver and plain member must be rejected (404 hides existence).
    other = client.request(
        "DELETE",
        f"/v1/documents/{document}",
        headers=headers("otherchief"),
        json={"expected_revision": 1},
    )
    assert other.status_code == 404
    member = client.request(
        "DELETE",
        f"/v1/documents/{document}",
        headers=headers("alice"),
        json={"expected_revision": 1},
    )
    assert member.status_code == 404
    # Stale revision is a conflict, not a delete.
    stale = client.request(
        "DELETE",
        f"/v1/documents/{document}",
        headers=headers("chief"),
        json={"expected_revision": 99},
    )
    assert stale.status_code == 409

    deleted = client.request(
        "DELETE",
        f"/v1/documents/{document}",
        headers=headers("chief"),
        json={"expected_revision": 1},
    )
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["deleted_versions"] == 1
    assert deleted.json()["quarantined"], "original blob must be quarantined"

    # Gone from search, versions, jobs, chunks; audit survives as the only trace.
    assert (
        client.post(
            "/v1/search", headers=headers("chief"), json={"query": "合成文件", "mode": "files"}
        ).json()["items"]
        == []
    )
    with db.connection() as conn:
        assert conn.execute("SELECT count(*) AS n FROM documents").fetchone()["n"] == 0
        assert conn.execute("SELECT count(*) AS n FROM versions").fetchone()["n"] == 0
        assert conn.execute("SELECT count(*) AS n FROM chunks").fetchone()["n"] == 0
        assert conn.execute("SELECT count(*) AS n FROM jobs").fetchone()["n"] == 0
        event = conn.execute(
            "SELECT actor_id,action,details FROM audit_events WHERE action='delete_document' ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        assert event["actor_id"] == "chief" and event["action"] == "delete_document"
        assert event["details"]["filenames"] == ["公司资料.md"]
    # The original file still exists but moved into the quarantine folder.
    quarantine = root / "quarantine"
    quarantined = list(quarantine.iterdir())
    assert quarantined and all(p.is_file() for p in quarantined)
    for path in storage_files:
        assert not path.exists(), f"{path} should have been moved to quarantine"


def test_publisher_discovers_withdrawn_file_without_retained_identifiers(kb):
    client, _, _ = kb
    created, _, _ = submit(client)
    assert publish(client, created.json()["version_id"], 0).status_code == 200
    assert (
        client.post(
            f"/v1/documents/{created.json()['document_id']}/withdraw",
            headers=headers("chief"),
            json={"expected_revision": 1},
        ).status_code
        == 200
    )
    # A fresh management session only knows the list endpoint, not document/version IDs.
    managed = client.get("/v1/documents?managed=true", headers=headers("chief")).json()["items"]
    assert len(managed) == 1
    doc = managed[0]
    history = client.get(f"/v1/documents/{doc['document_id']}/versions", headers=headers("chief"))
    assert history.status_code == 200
    version = history.json()["items"][0]
    assert version["state"] == "withdrawn"
    assert client.get("/v1/documents?managed=true", headers=headers("peer")).json()["items"] == []
    assert (
        client.get(
            f"/v1/documents/{doc['document_id']}/versions", headers=headers("peer")
        ).status_code
        == 404
    )
    assert (
        client.post(
            f"/v1/versions/{version['version_id']}/restore",
            headers=headers("chief"),
            json={"expected_revision": doc["revision"]},
        ).status_code
        == 200
    )

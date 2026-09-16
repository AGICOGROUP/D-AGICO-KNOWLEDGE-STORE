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

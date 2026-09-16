from conftest import headers, submit


def publish_category(client, version, category, revision=0, person="chief"):
    return client.post(
        f"/v1/versions/{version}/publish",
        headers=headers(person),
        json={"expected_revision": revision, "accept_incomplete": True, "category_id": category},
    )


def test_category_review_rejects_unauthorized_and_invalid_without_mutation(kb):
    client, db, _ = kb
    made, _, _ = submit(client, category_id="other")
    vid, did = made.json()["version_id"], made.json()["document_id"]
    for person in ("alice", "otherchief"):
        assert publish_category(client, vid, "background", person=person).status_code == 404
    invalid = publish_category(client, vid, "missing-category")
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "INVALID_ARGUMENT"
    with db.connection() as conn:
        assert conn.execute(
            "SELECT category_id,revision,active_version_id FROM documents WHERE id=%s", (did,)
        ).fetchone() == {"category_id": "other", "revision": 0, "active_version_id": None}
        assert (
            conn.execute("SELECT state FROM versions WHERE id=%s", (vid,)).fetchone()["state"]
            == "draft"
        )


def test_category_review_cannot_rebase_stale_draft(kb):
    client, db, _ = kb
    made, _, _ = submit(client, category_id="other")
    did = made.json()["document_id"]
    assert publish_category(client, made.json()["version_id"], "other").status_code == 200
    first, _, _ = submit(client, document_id=did, base_revision=1, category_id="other")
    stale, _, _ = submit(client, document_id=did, base_revision=1, category_id="other")
    assert (
        publish_category(client, first.json()["version_id"], "background", revision=1).status_code
        == 200
    )
    denied = publish_category(client, stale.json()["version_id"], "standards", revision=2)
    assert denied.status_code == 409
    assert denied.json()["error"]["code"] == "VERSION_CONFLICT"
    with db.connection() as conn:
        assert conn.execute(
            "SELECT category_id,revision FROM documents WHERE id=%s", (did,)
        ).fetchone() == {"category_id": "background", "revision": 2}

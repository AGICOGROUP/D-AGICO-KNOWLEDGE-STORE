from conftest import headers, submit


def test_linked_attachment_does_not_bypass_child_permissions(kb):
    client, _, _ = kb
    parent, _, _ = submit(client)
    child, _, _ = submit(client, visibility="private")
    for version in [parent, child]:
        client.post(
            f"/v1/versions/{version.json()['version_id']}/publish",
            headers=headers("chief"),
            json={"expected_revision": 0, "accept_incomplete": True},
        )
    pid, cid = parent.json()["version_id"], child.json()["version_id"]
    assert (
        client.post(
            f"/v1/versions/{pid}/links",
            headers=headers("chief"),
            json={"child_version_id": cid, "label": "参数附件"},
        ).status_code
        == 200
    )
    own = client.get(f"/v1/versions/{pid}/file", headers=headers()).json()
    assert own["links"][0]["version_id"] == cid
    peer = client.get(f"/v1/versions/{pid}/file", headers=headers("peer")).json()
    assert peer["links"] == [] and "blob_key" not in str(peer)


def test_retry_only_authorized_and_not_withdrawn(kb):
    client, _, _ = kb
    draft, _, _ = submit(client)
    vid = draft.json()["version_id"]
    assert client.post(f"/v1/versions/{vid}/retry", headers=headers("bob")).status_code == 404
    assert client.post(f"/v1/versions/{vid}/retry", headers=headers()).status_code == 200
    client.post(f"/v1/submissions/{vid}/withdraw", headers=headers())
    assert client.post(f"/v1/versions/{vid}/retry", headers=headers()).status_code == 404

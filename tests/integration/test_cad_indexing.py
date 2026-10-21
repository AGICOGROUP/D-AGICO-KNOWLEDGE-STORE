from uuid import uuid4

import ezdxf
from conftest import headers

from agico_kb.worker import Worker


def test_published_dxf_is_indexed_by_worker_and_searchable(kb_large, real_embedder, tmp_path):
    client, db, _ = kb_large
    drawing = ezdxf.new()
    drawing.modelspace().add_text("UNIQUE-PUMP-731", dxfattribs={"layer": "TITLE"})
    source = tmp_path / "fresh.dxf"
    drawing.saveas(source)
    content = source.read_bytes()
    prepared = client.post(
        "/v1/uploads", headers=headers(key=uuid4().hex),
        json={"filename": "fresh.dxf", "size": len(content)},
    )
    assert prepared.status_code == 201, prepared.text
    upload_id = prepared.json()["upload_id"]
    assert client.put(
        f"/v1/uploads/{upload_id}/content", headers=headers(), content=content
    ).status_code == 200
    submitted = client.post(
        "/v1/submissions", headers=headers(),
        json={"upload_id": upload_id, "organization_id": "baiste", "title": "Fresh drawing"},
    )
    assert submitted.status_code == 201, submitted.text
    version_id = submitted.json()["version_id"]
    assert submitted.json()["processing_status"] == "queued"
    premature = client.post(
        f"/v1/versions/{version_id}/publish", headers=headers("chief"),
        json={"expected_revision": 0},
    )
    assert premature.status_code == 409
    assert Worker(db, client.app.state.settings, embedder=real_embedder).run_once()
    with db.connection() as conn:
        version = conn.execute(
            "SELECT processing_status,publication_mode FROM versions WHERE id=%s", (version_id,)
        ).fetchone()
        chunk = conn.execute(
            "SELECT text,locator FROM chunks WHERE version_id=%s", (version_id,)
        ).fetchone()
    assert version["processing_status"] == "ready"
    approved = client.post(
        f"/v1/versions/{version_id}/publish", headers=headers("chief"),
        json={"expected_revision": 0},
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["publication_mode"] == "content"
    assert "UNIQUE-PUMP-731" in chunk["text"]
    assert chunk["locator"]["layer"] == "TITLE"
    found = client.post(
        "/v1/search", headers=headers("peer"),
        json={"mode": "content", "query": "UNIQUE-PUMP-731"},
    )
    assert found.status_code == 200, found.text
    assert any(item["version_id"] == version_id for item in found.json()["items"])
    read = client.post('/v1/read', headers=headers('peer'), json={'version_id': version_id})
    assert read.status_code == 200, read.text
    assert read.json()['requires_file'] is True
    assert client.get(f"/v1/versions/{version_id}/content", headers=headers("chief")).content == content

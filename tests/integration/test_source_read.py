from conftest import headers
from test_search import index_text


def test_read_page_keeps_units_and_has_continuation(kb, real_embedder):
    client, _, _ = kb
    version = index_text(
        kb, "# 接线要求\n\n接线电压必须为 220 V。\n\n注意：断电后才可操作。", real_embedder
    )
    response = client.post(
        "/v1/read", headers=headers(), json={"version_id": version["version_id"], "limit": 1}
    )
    assert response.json()["has_more"]
    result = client.post(
        "/v1/read",
        headers=headers(),
        json={"version_id": version["version_id"], "offset": response.json()["next_offset"]},
    )
    text = "\n".join(item["text"] for item in result.json()["items"])
    assert "220 V" in text and "断电后" in text


def test_rebuilt_index_rejects_stale_chunk_identifier(kb, real_embedder):
    from agico_kb.worker import Worker

    client, db, _ = kb
    version = index_text(kb, "设备运行前必须检查接地。", real_embedder)
    hit = client.post("/v1/search", headers=headers(), json={"query": "接地"}).json()["items"][0]
    worker = Worker(db, client.app.state.settings, embedder=real_embedder)
    worker.retry(version["version_id"])
    assert worker.run_once()
    response = client.post(
        "/v1/read",
        headers=headers(),
        json={"version_id": version["version_id"], "chunk_id": hit["chunk_id"]},
    )
    assert response.status_code == 409


def test_model_identity_mismatch_falls_back_without_mixing_spaces(kb, real_embedder):
    client, db, _ = kb
    version = index_text(kb, "型号 AX-210 需要定期检查润滑。", real_embedder)
    with db.connection() as conn:
        conn.execute(
            "UPDATE chunks SET model_identity='different-model' WHERE version_id=%s",
            (version["version_id"],),
        )
    result = client.post("/v1/search", headers=headers(), json={"query": "润滑"})
    assert result.json()["items"] and any("模型" in w for w in result.json()["warnings"])

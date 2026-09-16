from uuid import uuid4

import pytest
from conftest import headers

from agico_kb.worker import Worker


@pytest.mark.parametrize("field", ["title", "filename"])
def test_file_only_search_normalizes_both_query_and_metadata(kb, field):
    import unicodedata

    client, db, _ = kb
    label = "１０ＴＰＨ工艺流程图（不含磨机）_Straße"
    raw = uuid4().bytes
    filename = label + ".dwg" if field == "filename" else "opaque.dwg"
    prepared = client.post(
        "/v1/uploads",
        headers=headers("alice", uuid4().hex),
        json={"filename": filename, "size": len(raw)},
    )
    assert prepared.status_code == 201
    upload = prepared.json()
    assert client.put(upload["upload_url"], headers=headers(), content=raw).status_code == 200
    submitted = client.post(
        "/v1/submissions",
        headers=headers(),
        json={
            "upload_id": upload["upload_id"],
            "organization_id": "baiste",
            "title": label if field == "title" else "opaque",
        },
    )
    assert submitted.status_code == 201
    version = submitted.json()["version_id"]
    assert Worker(db, client.app.state.settings).run_once()
    assert (
        client.post(
            f"/v1/versions/{version}/publish",
            headers=headers("chief"),
            json={"expected_revision": 0, "accept_incomplete": True},
        ).status_code
        == 200
    )
    for query in (label, unicodedata.normalize("NFKC", label).lower()):
        response = client.post(
            "/v1/search", headers=headers("peer"), json={"mode": "files", "query": query}
        )
        assert response.status_code == 200
        assert [x["version_id"] for x in response.json()["items"]] == [version]
        denied = client.post(
            "/v1/search", headers=headers("bob"), json={"mode": "files", "query": query}
        )
        assert denied.json()["items"] == []


def index_text(kb, text, embedder, **metadata):
    client, db, _ = kb
    person = metadata.pop("person", "alice")
    chief = metadata.pop("chief", "chief")
    data = (text + "\n\n合成记录标识：" + uuid4().hex).encode()
    r = client.post(
        "/v1/uploads",
        headers=headers(person, uuid4().hex),
        json={"filename": "知识.md", "size": len(data)},
    )
    uid = r.json()["upload_id"]
    assert (
        client.put(f"/v1/uploads/{uid}/content", headers=headers(person), content=data).status_code
        == 200
    )
    body = {
        "upload_id": uid,
        "organization_id": "baiste",
        "category_id": "technical",
        "title": "示例资料",
        **metadata,
    }
    r = client.post("/v1/submissions", headers=headers(person), json=body)
    assert r.status_code == 201, r.text
    vid = r.json()["version_id"]
    worker = Worker(db, client.app.state.settings, embedder=embedder)
    assert worker.run_once()
    r = client.post(
        f"/v1/versions/{vid}/publish",
        headers=headers(chief),
        json={"expected_revision": body.get("base_revision", 0), "accept_incomplete": True},
    )
    assert r.status_code == 200, r.text
    return r.json()


def test_hybrid_retrieval_exact_model_source_and_permissions(kb, real_embedder):
    client, _, _ = kb
    target = index_text(
        kb, "AX-210 最高工作温度为 80 摄氏度，仅适用标准配置。", real_embedder, model="AX-210"
    )
    index_text(kb, "AX-210B 最高工作温度为 120 摄氏度。", real_embedder, model="AX-210B")
    private = index_text(
        kb, "保密：AX-210 特制配置温度为 300 摄氏度。", real_embedder, visibility="private"
    )
    # A real Chinese paraphrase is matched by local vector+keyword retrieval.
    r = client.post(
        "/v1/search",
        headers=headers("peer"),
        json={"query": "这种设备能承受多高的温度", "model": "ax-210"},
    )
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert items and all(i["version_id"] == target["version_id"] for i in items)
    assert any("80 摄氏度" in i["excerpt"] for i in items)
    assert not any(i["version_id"] == private["version_id"] for i in items)
    hit = next(i for i in items if "80 摄氏度" in i["excerpt"])
    read = client.post(
        "/v1/read",
        headers=headers("peer"),
        json={"version_id": hit["version_id"], "chunk_id": hit["chunk_id"]},
    )
    assert read.status_code == 200 and "标准配置" in read.json()["items"][0]["text"]
    assert read.json()["items"][0]["locator"] == hit["locator"]
    assert (
        client.post("/v1/search", headers=headers("bob"), json={"query": "温度"}).json()["items"]
        == []
    )


def test_distinct_historical_records_remain_searchable(kb, real_embedder):
    client, _, _ = kb
    old = index_text(
        kb,
        "2022 年项目经验：现场电压波动，需先核对供电条件。",
        real_embedder,
        category_id="experience",
        business_date="2022-01-02",
    )
    new = index_text(
        kb,
        "2026 年项目经验：现场交付前完成接线检查。",
        real_embedder,
        category_id="experience",
        business_date="2026-06-03",
    )
    r = client.post(
        "/v1/search",
        headers=headers(),
        json={"query": "项目经验", "mode": "files", "category_id": "experience"},
    )
    assert {i["version_id"] for i in r.json()["items"]} == {old["version_id"], new["version_id"]}
    assert {i["business_date"] for i in r.json()["items"]} == {"2022-01-02", "2026-06-03"}


def test_file_mode_and_keyword_fallback_do_not_require_model(kb, real_embedder):
    client, _, _ = kb
    target = index_text(
        kb, "报销规范：差旅报销需要发票和部门负责人审核。", real_embedder, category_id="standards"
    )
    service = client.app.state.search

    class NoModel:
        def query(self, text):
            raise TimeoutError("injected query timeout")

        def close(self):
            pass

    service.query_model = NoModel()
    r = client.post("/v1/search", headers=headers(), json={"query": "发票"})
    assert r.status_code == 200 and r.json()["warnings"]
    assert r.json()["items"][0]["version_id"] == target["version_id"]
    r = client.post("/v1/search", headers=headers(), json={"query": "示例资料", "mode": "files"})
    assert r.json()["items"] and not r.json()["warnings"]


def test_withdrawn_and_superseded_content_never_return(kb, real_embedder):
    client, _, _ = kb
    old = index_text(kb, "温度限制 80 摄氏度。", real_embedder)
    new = index_text(
        kb, "温度限制 90 摄氏度。", real_embedder, document_id=old["document_id"], base_revision=1
    )
    r = client.post("/v1/search", headers=headers(), json={"query": "温度"})
    assert {i["version_id"] for i in r.json()["items"]} == {new["version_id"]}
    assert (
        client.post(
            "/v1/read", headers=headers(), json={"version_id": old["version_id"]}
        ).status_code
        == 404
    )
    client.post(
        f"/v1/documents/{new['document_id']}/withdraw",
        headers=headers("chief"),
        json={"expected_revision": 2},
    )
    assert (
        client.post("/v1/search", headers=headers(), json={"query": "温度"}).json()["items"] == []
    )
    assert (
        client.post(
            "/v1/read", headers=headers(), json={"version_id": new["version_id"]}
        ).status_code
        == 404
    )


def test_empty_context_and_context_scope(kb, real_embedder):
    client, _, _ = kb
    assert client.post("/v1/context", headers=headers(), json={}).json()["items"] == []
    bg = index_text(
        kb,
        "公司业务背景：合成企业提供工业设备，交付资料应明确产品型号。",
        real_embedder,
        category_id="background",
    )
    result = client.post("/v1/context", headers=headers(), json={"organization_id": "baiste"})
    assert result.json()["items"][0]["version_id"] == bg["version_id"]


def test_hybrid_pages_keep_fixed_rank_pool_and_do_not_skip_or_repeat(kb):
    from test_worker_recovery import UnavailableEmbedding

    client, db, _ = kb
    versions = []
    vectors = ["[0.8,0.6,0]", "[0.9,0.4358899,0]", "[1,0,0]"]
    for repeats, vector in zip([3, 2, 1], vectors, strict=True):
        version = index_text(kb, "key " * repeats, UnavailableEmbedding())
        versions.append(version["version_id"])
        with db.connection() as conn:
            conn.execute(
                "UPDATE chunks SET embedding=%s::public.vector,model_identity='rank-test' WHERE version_id=%s AND text LIKE 'key%%'",
                (vector, version["version_id"]),
            )

    class ControlledQuery:
        def query(self, text):
            return [1, 0, 0], "rank-test"

        def close(self):
            pass

    client.app.state.search.query_model = ControlledQuery()
    seen = []
    offset = 0
    for _ in range(3):
        page = client.post(
            "/v1/search", headers=headers(), json={"query": "key", "limit": 1, "offset": offset}
        ).json()
        seen.extend(item["version_id"] for item in page["items"])
        offset = page["next_offset"]
    assert len(seen) == 3 and set(seen) == set(versions)

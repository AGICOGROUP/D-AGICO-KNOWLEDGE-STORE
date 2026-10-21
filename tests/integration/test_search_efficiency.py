"""New synthetic documents exercise hot-query authorization and query counts."""

from contextlib import contextmanager

from conftest import headers
from test_search import index_text

from agico_kb.auth import Principal
from agico_kb.contracts import SearchRequest


def test_page_permission_recheck_has_constant_database_round_trips(kb, real_embedder):
    client, db, _ = kb
    for number in range(5):
        index_text(kb, f"测试数据 搜索效率：轴承保养说明 第 {number} 项。", real_embedder)
    service = client.app.state.search
    principal = Principal("peer", {"baiste": "member"})
    request = SearchRequest(query="轴承保养说明")
    rows = service.search(principal, request)["items"]
    assert len(rows) == 5
    statements = []

    class CountingConnection:
        def __init__(self, connection):
            self.connection = connection

        def execute(self, statement, *args, **kwargs):
            statements.append(statement)
            return self.connection.execute(statement, *args, **kwargs)

    class CountingDatabase:
        @contextmanager
        def connection(self):
            with db.connection() as connection:
                yield CountingConnection(connection)

    service.db = CountingDatabase()
    try:
        response = service._page(principal, rows, request, [])
    finally:
        service.db = db
    assert len(response["items"]) == 5
    assert len(statements) <= 1, "Final authorization performs one database round trip per hit"


def test_cached_query_rechecks_revoked_grants_and_department_membership(kb, real_embedder):
    client, db, _ = kb
    target = index_text(kb, "测试数据：阀门保养时先切断电源。", real_embedder, visibility="private")
    model_calls = []

    class CountedModel:
        identity = real_embedder.identity

        def embed(self, texts):
            model_calls.extend(texts)
            return real_embedder.embed(texts)

    client.app.state.search.query_model._model = CountedModel()

    def search():
        response = client.post("/v1/search", headers=headers("peer"), json={"query": "阀门保养"})
        assert response.status_code == 200
        return response.json()["items"]

    assert search() == []
    grant = client.put(f"/v1/documents/{target['document_id']}/grants", headers=headers("chief"),
                       json={"expected_revision": 1, "principal_ids": ["peer"]})
    assert grant.status_code == 200, grant.text
    assert search()
    revoke = client.put(f"/v1/documents/{target['document_id']}/grants", headers=headers("chief"),
                        json={"expected_revision": 2, "principal_ids": []})
    assert revoke.status_code == 200, revoke.text
    assert search() == []
    # A department-visible document also disappears from the very same cached query.
    index_text(kb, "测试数据：阀门保养检查密封件。", real_embedder)
    assert search()
    with db.connection() as conn:
        conn.execute("DELETE FROM memberships WHERE principal_id='peer'")
    assert search() == []
    assert model_calls == ["阀门保养"]


def test_content_search_only_fetches_source_text_for_requested_page(kb):
    from test_worker_recovery import UnavailableEmbedding

    client, db, _ = kb
    for number in range(8):
        index_text(kb, f"测试数据：资料定位效率，第 {number} 条完整原文。", UnavailableEmbedding())
    source_rows = []

    class CountingCursor:
        def __init__(self, cursor, statement):
            self.cursor, self.statement = cursor, statement

        def fetchall(self):
            rows = self.cursor.fetchall()
            if "AS excerpt" in self.statement:
                source_rows.append(len(rows))
            return rows

        def fetchone(self):
            return self.cursor.fetchone()

    class CountingConnection:
        def __init__(self, connection):
            self.connection = connection

        def execute(self, statement, *args, **kwargs):
            return CountingCursor(self.connection.execute(statement, *args, **kwargs), statement)

    class CountingDatabase:
        @contextmanager
        def connection(self):
            with db.connection() as connection:
                yield CountingConnection(connection)

    class NoModel:
        def query(self, text):
            raise TimeoutError("测试数据：仅检查关键词检索的数据传输量")

        def close(self):
            pass

    service = client.app.state.search
    service.query_model.close()
    service.query_model = NoModel()
    service.db = CountingDatabase()
    try:
        response = service.search(Principal("peer", {"baiste": "member"}),
                                  SearchRequest(query="资料定位效率", limit=3, offset=2))
    finally:
        service.db = db
    assert len(response["items"]) == 3
    assert response["has_more"]
    assert all("完整原文" in row["excerpt"] and row["locator"] for row in response["items"])
    assert source_rows and max(source_rows) <= 3, "Unselected candidates still transfer source text"

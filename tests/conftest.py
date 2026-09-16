import hashlib
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg import sql
from psycopg.conninfo import make_conninfo

from agico_kb.config import Settings
from agico_kb.main import create_app


def make_kb(tmp_path, max_upload_bytes=1024):
    # This fixture is deliberately confined to the named, local development test DB.
    password = Path(".local/pg-password.txt").read_text().strip()
    dsn = make_conninfo(
        host="127.0.0.1", port=15432, dbname="agico_test", user="agico_dev", password=password
    )
    schema = "test_" + uuid4().hex
    with psycopg.connect(dsn, autocommit=True) as conn:
        assert conn.info.dbname == "agico_test"
        conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    app = create_app(Settings(dsn, tmp_path, schema, max_upload_bytes=max_upload_bytes))
    app.state.db.migrate()
    with app.state.db.connection() as conn:
        for person, org, role in [
            ("alice", "baiste", "member"),
            ("peer", "baiste", "member"),
            ("bob", "xingyuan", "member"),
            ("chief", "baiste", "publisher"),
            ("otherchief", "xingyuan", "publisher"),
        ]:
            conn.execute("INSERT INTO principals(id,name) VALUES (%s,%s)", (person, person))
            conn.execute("INSERT INTO memberships VALUES (%s,%s,%s)", (person, org, role))
            conn.execute(
                "INSERT INTO access_tokens(token_hash,principal_id) VALUES (%s,%s)",
                (hashlib.sha256(person.encode()).hexdigest(), person),
            )
    with TestClient(app) as client:
        yield client, app.state.db, tmp_path
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


@pytest.fixture
def kb(tmp_path):
    yield from make_kb(tmp_path)


@pytest.fixture
def kb_large(tmp_path):
    yield from make_kb(tmp_path, 8 * 1024 * 1024)


@pytest.fixture(scope="session")
def real_embedder():
    from agico_kb.embeddings import LocalEmbedding

    return LocalEmbedding(Settings("unused", Path(".local/probe")))


def headers(person="alice", key=None):
    result = {"Authorization": "Bearer " + person}
    if key:
        result["Idempotency-Key"] = key
    return result


def submit(client, person="alice", **overrides):
    content = ("全新合成资料 " + uuid4().hex).encode()
    response = client.post(
        "/v1/uploads",
        headers=headers(person, uuid4().hex),
        json={"filename": "公司资料.md", "size": len(content)},
    )
    assert response.status_code == 201, response.text
    upload_id = response.json()["upload_id"]
    response = client.put(
        f"/v1/uploads/{upload_id}/content", headers=headers(person), content=content
    )
    assert response.status_code == 200, response.text
    body = {
        "upload_id": upload_id,
        "organization_id": "baiste",
        "category_id": "technical",
        "title": "合成文件",
        "visibility": "department",
        "base_revision": 0,
    }
    body.update(overrides)
    response = client.post("/v1/submissions", headers=headers(person), json=body)
    return response, body, content

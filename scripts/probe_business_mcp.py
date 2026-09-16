"""Temporary real-business-tool server, fresh synthetic data, isolated test schema."""

import secrets
from pathlib import Path
from uuid import uuid4

import psycopg
import uvicorn
from fastapi.testclient import TestClient
from psycopg import sql
from psycopg.conninfo import make_conninfo

from agico_kb.auth import token_hash
from agico_kb.config import Settings
from agico_kb.embeddings import LocalEmbedding
from agico_kb.main import create_app
from agico_kb.worker import Worker


def main():
    root = Path(__file__).resolve().parents[1]
    password = (root / ".local/pg-password.txt").read_text().strip()
    dsn = make_conninfo(
        host="127.0.0.1", port=15432, dbname="agico_test", user="agico_dev", password=password
    )
    schema = "probe_" + uuid4().hex
    settings = Settings(
        dsn, root / ".local/business-probe" / schema, schema, model_cache=root / ".local/models"
    )
    reader, publisher = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    with psycopg.connect(dsn, autocommit=True) as conn:
        assert conn.info.dbname == "agico_test"
        conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    try:
        setup = create_app(settings)
        setup.state.db.migrate()
        with setup.state.db.connection() as conn:
            for person, role, token in [
                ("reader", "member", reader),
                ("publisher", "publisher", publisher),
            ]:
                conn.execute(
                    "INSERT INTO principals(id,name) VALUES (%s,%s)", (person, "合成测试身份")
                )
                conn.execute("INSERT INTO memberships VALUES (%s,%s,%s)", (person, "baiste", role))
                conn.execute(
                    "INSERT INTO access_tokens(token_hash,principal_id) VALUES (%s,%s)",
                    (token_hash(token), person),
                )
        with TestClient(setup) as client:
            h = {"Authorization": "Bearer " + publisher}
            text = "合成测试资料，不代表公司真实产品。\n\nAX-210 的最高工作温度为 80 摄氏度。此参数仅适用于标准配置，不适用于防爆环境。"
            data = text.encode()
            upload = client.post(
                "/v1/uploads",
                headers={**h, "Idempotency-Key": uuid4().hex},
                json={"filename": "合成产品参数.md", "size": len(data)},
            ).json()
            client.put(upload["upload_url"], headers=h, content=data).raise_for_status()
            response = client.post(
                "/v1/submissions",
                headers=h,
                json={
                    "upload_id": upload["upload_id"],
                    "organization_id": "baiste",
                    "category_id": "technical",
                    "title": "合成产品参数",
                    "model": "AX-210",
                },
            )
            response.raise_for_status()
            version = response.json()["version_id"]
            Worker(setup.state.db, settings, LocalEmbedding(settings)).run_once()
            client.post(
                f"/v1/versions/{version}/publish", headers=h, json={"expected_revision": 0}
            ).raise_for_status()
        (root / ".local/probe/business-reader-token.txt").write_text(reader, encoding="utf-8")
        print("Business MCP probe ready: synthetic data, restricted reader, port 18766", flush=True)
        uvicorn.run(create_app(settings), host="127.0.0.1", port=18766, log_level="warning")
    finally:
        with psycopg.connect(dsn, autocommit=True) as conn:
            conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
        (root / ".local/probe/business-reader-token.txt").unlink(missing_ok=True)


if __name__ == "__main__":
    main()

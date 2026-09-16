"""Initialize databases in the project's loopback-only development cluster."""

import json
from pathlib import Path

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo

root = Path(__file__).resolve().parents[1]
password = (root / ".local/pg-password.txt").read_text(encoding="utf-8").strip()
dsn = make_conninfo(
    host="127.0.0.1", port=15432, user="agico_dev", password=password, dbname="postgres"
)
with psycopg.connect(dsn, autocommit=True) as conn:
    for name in ("agico_test", "agico_dev"):
        if not conn.execute("SELECT 1 FROM pg_database WHERE datname=%s", (name,)).fetchone():
            conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
with psycopg.connect(make_conninfo(dsn, dbname="agico_test")) as conn:
    conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
    version = conn.execute("SELECT extversion FROM pg_extension WHERE extname='vector'").fetchone()[
        0
    ]
    distance = conn.execute("SELECT '[1,0,0]'::vector <=> '[1,0,0]'::vector").fetchone()[0]
    assert distance == 0
    print(
        json.dumps(
            {"postgres": conn.info.server_version, "pgvector": version, "cosine_distance": distance}
        )
    )

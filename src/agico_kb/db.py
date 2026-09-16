import json
import re
from contextlib import contextmanager
from importlib.resources import files
from threading import Lock

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from .config import Settings
from .errors import KBError


class Database:
    def __init__(self, settings: Settings):
        if not re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_]{0,62}", settings.schema):
            raise ValueError("Invalid database schema")
        self.settings = settings
        self.pool = ConnectionPool(
            settings.database_url,
            open=False,
            min_size=0,
            max_size=8,
            check=ConnectionPool.check_connection,
            kwargs={"row_factory": dict_row, "options": f"-csearch_path={settings.schema},public"},
        )
        self._lock = Lock()
        self._opened = False

    @contextmanager
    def connection(self, *, write=False):
        with self._lock:
            if not self._opened:
                self.pool.open()
                self._opened = True
        with self.pool.connection() as conn:
            if write:
                allowed = conn.execute(
                    "SELECT pg_try_advisory_xact_lock_shared(hashtextextended(%s, 0)) AS allowed",
                    (self.settings.schema + ":backup",),
                ).fetchone()["allowed"]
                if not allowed:
                    raise KBError(
                        "MAINTENANCE", "备份期间暂缓写入，请稍后重试。", 503, retriable=True
                    )
            yield conn

    def close(self):
        self.pool.close()

    def migrate(self):
        with self.connection(write=True) as conn:
            conn.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(current_schema() || ':migrate', 0))"
            )
            conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations (version integer PRIMARY KEY)"
            )
            if not conn.execute("SELECT 1 FROM schema_migrations WHERE version=1").fetchone():
                conn.execute(
                    files("agico_kb")
                    .joinpath("migrations/001_initial.sql")
                    .read_text(encoding="utf-8")
                )
                conn.execute("INSERT INTO schema_migrations VALUES (1)")
            if not conn.execute("SELECT 1 FROM schema_migrations WHERE version=2").fetchone():
                conn.execute(
                    files("agico_kb")
                    .joinpath("migrations/002_ingestion.sql")
                    .read_text(encoding="utf-8")
                )
                conn.execute("INSERT INTO schema_migrations VALUES (2)")
            data = json.loads(
                files("agico_kb").joinpath("taxonomy.json").read_text(encoding="utf-8")
            )
            for key, name in data["organizations"].items():
                conn.execute(
                    "INSERT INTO organizations VALUES (%s,%s) ON CONFLICT(id) DO UPDATE SET name=excluded.name",
                    (key, name),
                )
            for key, name in data["categories"].items():
                conn.execute(
                    "INSERT INTO categories VALUES (%s,%s) ON CONFLICT(id) DO UPDATE SET name=excluded.name",
                    (key, name),
                )

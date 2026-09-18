"""Initialize an explicitly owned, new server installation; never print credentials."""

import argparse
import json
import os
import re
import secrets
import subprocess
import sys
import traceback
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo

from .admin import issue_token, set_identity
from .config import Settings
from .db import Database


def load_deployment(app_root, data_root, api_port, database_port, service_prefix):
    app_root, data_root = Path(app_root).resolve(), Path(data_root).resolve()
    if (
        not str(app_root).isascii()
        or app_root == data_root
        or app_root in data_root.parents
        or data_root in app_root.parents
        or data_root.parent == data_root
        or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{2,40}", service_prefix)
        or not 1 <= api_port <= 65535
        or not 1 <= database_port <= 65535
        or api_port == database_port
    ):
        raise ValueError("Invalid installation paths, ports, or service prefix")
    marker = json.loads((data_root / "config/deployment.json").read_text(encoding="utf-8-sig"))
    expected = {
        "schema_version": 1,
        "api_port": api_port,
        "database_port": database_port,
        "service_prefix": service_prefix,
    }
    if (
        any(marker.get(key) != value for key, value in expected.items())
        or Path(marker["app_root"]).resolve() != app_root
        or Path(marker["data_root"]).resolve() != data_root
        or marker.get("stage") not in {"preparing", "initialized", "installed"}
    ):
        raise ValueError("Existing deployment does not match the requested installation")
    return marker


def write_json_once(path, content):
    """Atomic create without overwriting a previous successful credential write."""
    path = Path(path)
    temp = path.with_name(path.name + "." + uuid4().hex + ".tmp")
    try:
        with temp.open("x", encoding="utf-8") as stream:
            json.dump(content, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def _replace_json(path, content):
    temp = path.with_name(path.name + "." + uuid4().hex + ".tmp")
    try:
        write_json_once(temp, content)
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


@contextmanager
def installation_lock(data_root):
    """Also fence direct initializer calls while bootstrap owns its outer mutex."""
    with (Path(data_root) / "config/initializer.lock").open("a+b") as lock:
        if lock.seek(0, os.SEEK_END) == 0:
            lock.write(b"0")
            lock.flush()
        lock.seek(0)
        if os.name == "nt":
            import msvcrt

            try:
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                raise RuntimeError(
                    "An initializer is already running for this data folder"
                ) from None
            try:
                yield
            finally:
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            try:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                raise RuntimeError(
                    "An initializer is already running for this data folder"
                ) from None
            try:
                yield
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def initialize(app_root, data_root, api_port, database_port, service_prefix):
    load_deployment(app_root, data_root, api_port, database_port, service_prefix)
    with installation_lock(data_root):
        return _initialize_owned(app_root, data_root, api_port, database_port, service_prefix)


def _initialize_owned(app_root, data_root, api_port, database_port, service_prefix):
    app_root, data_root = Path(app_root).resolve(), Path(data_root).resolve()
    marker = load_deployment(app_root, data_root, api_port, database_port, service_prefix)
    config_root = data_root / "config"
    runtime_path = config_root / "runtime.json"
    access_path = config_root / "initial-access.json"
    alias = app_root / ".runtime/data"
    if not alias.is_dir() or alias.resolve() != data_root:
        raise ValueError("Managed data junction is missing or points elsewhere")
    pg_data = alias / "postgres"
    pg_bin = app_root / ".runtime/postgres/Library/bin"
    pg_ctl = pg_bin / "pg_ctl.exe"
    if marker["stage"] in {"initialized", "installed"}:
        for required in (runtime_path, access_path, pg_data / "PG_VERSION"):
            if not required.is_file():
                raise ValueError("Existing initialized deployment is incomplete; refusing reset")
        print("Existing initialized database and credentials preserved.", flush=True)
        return
    for name in ("originals", "models", "logs", "services"):
        (data_root / name).mkdir(exist_ok=True)
    env = {**os.environ, "PATH": str(pg_bin) + os.pathsep + os.environ.get("PATH", "")}
    log_path = data_root / "logs/setup.log"

    def command(args, timeout=180):
        with log_path.open("ab") as log:
            result = subprocess.run(
                [str(a) for a in args],
                cwd=app_root,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=log,
                timeout=timeout,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                check=False,
            )
        if result.returncode:
            raise RuntimeError("Database setup command failed; inspect protected setup log")

    maintenance_path = config_root / "maintenance.json"
    if not maintenance_path.exists():
        if (pg_data / "PG_VERSION").exists():
            raise ValueError("Database already exists without its owned maintenance config")
        owner_password = secrets.token_urlsafe(36)
        write_json_once(
            maintenance_path,
            {
                "database_url": make_conninfo(
                    host="127.0.0.1",
                    port=database_port,
                    dbname="postgres",
                    user="agico_owner",
                    password=owner_password,
                ),
                "initialization_password": owner_password,
            },
        )
    maintenance = json.loads(maintenance_path.read_text(encoding="utf-8"))
    owner_dsn = maintenance["database_url"]
    if not runtime_path.exists():
        write_json_once(
            runtime_path,
            {
                "AGICO_KB_DATABASE_URL": make_conninfo(
                    host="127.0.0.1",
                    port=database_port,
                    dbname="agico_kb",
                    user="agico_app",
                    password=secrets.token_urlsafe(36),
                ),
                "AGICO_KB_STORAGE_ROOT": str(data_root / "originals"),
                "AGICO_KB_SCHEMA": "public",
                "AGICO_KB_MODEL_CACHE": str(data_root / "models"),
                "AGICO_KB_MODEL_OFFLINE": "true",
                "AGICO_KB_EXPECTED_MODEL_IDENTITY": "",
                "AGICO_KB_ALLOWED_HOSTS": "127.0.0.1:*,localhost:*",
            },
        )
    runtime = json.loads(runtime_path.read_text(encoding="utf-8"))
    if not (pg_data / "PG_VERSION").exists():
        if pg_data.exists() and any(pg_data.iterdir()):
            raise ValueError("Nonempty incomplete PostgreSQL directory; refusing overwrite")
        password_file = alias / "config/bootstrap-password.tmp"
        try:
            password_file.write_text(maintenance["initialization_password"], encoding="ascii")
            command(
                [
                    pg_bin / "initdb.exe",
                    "-D",
                    pg_data,
                    "-U",
                    "agico_owner",
                    "-A",
                    "scram-sha-256",
                    "--encoding=UTF8",
                    "--locale=C",
                    "--pwfile=" + str(password_file),
                ]
            )
        finally:
            password_file.unlink(missing_ok=True)
    running = (
        subprocess.run(
            [str(pg_ctl), "-D", str(pg_data), "status"],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        ).returncode
        == 0
    )
    stop_owned = False
    try:
        if not running:
            command(
                [
                    pg_ctl,
                    "-D",
                    pg_data,
                    "-l",
                    alias / "logs/postgres-setup.log",
                    "-o",
                    f"-h 127.0.0.1 -p {database_port}",
                    "-w",
                    "start",
                ],
                timeout=60,
            )
            stop_owned = True
        with psycopg.connect(owner_dsn, autocommit=True, connect_timeout=10) as conn:
            active_dir = Path(conn.execute("SHOW data_directory").fetchone()[0])
            if active_dir.resolve() != (data_root / "postgres").resolve():
                # Do not stop an unverified, previously running server.
                raise ValueError("Connected PostgreSQL does not belong to this installation")
            stop_owned = True
            from psycopg.conninfo import conninfo_to_dict

            app_credentials = conninfo_to_dict(runtime["AGICO_KB_DATABASE_URL"])
            if not conn.execute("SELECT 1 FROM pg_roles WHERE rolname='agico_app'").fetchone():
                conn.execute(
                    sql.SQL(
                        "CREATE ROLE agico_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD {}"
                    ).format(sql.Literal(app_credentials["password"]))
                )
            if not conn.execute("SELECT 1 FROM pg_database WHERE datname='agico_kb'").fetchone():
                conn.execute("CREATE DATABASE agico_kb OWNER agico_app")
        with psycopg.connect(make_conninfo(owner_dsn, dbname="agico_kb")) as conn:
            conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
        settings = Settings(
            runtime["AGICO_KB_DATABASE_URL"],
            data_root / "originals",
            model_cache=data_root / "models",
            model_offline=False,
            expected_model_identity=runtime["AGICO_KB_EXPECTED_MODEL_IDENTITY"] or None,
        )
        db = Database(settings)
        try:
            db.migrate()
            if not access_path.exists():
                with db.connection() as conn:
                    organizations = conn.execute("SELECT id FROM organizations").fetchall()
                for organization in organizations:
                    set_identity(
                        db,
                        "bootstrap-admin",
                        "Initial local administrator",
                        organization["id"],
                        "publisher",
                    )
                with db.connection(write=True) as conn:
                    conn.execute("UPDATE principals SET is_admin=true WHERE id='bootstrap-admin'")
                expires = datetime.now(UTC) + timedelta(days=30)
                write_json_once(
                    access_path,
                    {
                        "identity": "bootstrap-admin",
                        "token": issue_token(db, "bootstrap-admin", expires),
                        "expires_at": expires.isoformat(),
                        "mcp_url": f"http://127.0.0.1:{api_port}/mcp",
                        "usage": "Local initialization only; issue separate identities for employees and agents.",
                    },
                )
        finally:
            db.close()
        print("Database initialized. Provisioning local embedding and OCR models...", flush=True)
        from .embeddings import LocalEmbedding
        from .ingestion import _ocr_engine

        model = LocalEmbedding(settings)
        runtime["AGICO_KB_EXPECTED_MODEL_IDENTITY"] = model.identity
        previous_cache = os.environ.get("AGICO_KB_MODEL_CACHE")
        try:
            os.environ["AGICO_KB_MODEL_CACHE"] = str(data_root / "models")
            _ocr_engine.cache_clear()
            _ocr_engine()
        finally:
            _ocr_engine.cache_clear()
            if previous_cache is None:
                os.environ.pop("AGICO_KB_MODEL_CACHE", None)
            else:
                os.environ["AGICO_KB_MODEL_CACHE"] = previous_cache
        _replace_json(runtime_path, runtime)
    finally:
        if stop_owned:
            command([pg_ctl, "-D", pg_data, "-m", "fast", "-w", "stop"], timeout=60)
    marker["stage"] = "initialized"
    _replace_json(config_root / "deployment.json", marker)
    print(
        "Initialization complete. Credentials are in the protected data/config directory.",
        flush=True,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app-root", type=Path, required=True)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--api-port", type=int, default=8765)
    parser.add_argument("--database-port", type=int, default=15432)
    parser.add_argument("--service-prefix", default="AgicoKb")
    args = parser.parse_args()
    try:
        initialize(
            args.app_root, args.data_root, args.api_port, args.database_port, args.service_prefix
        )
    except Exception:  # noqa: BLE001 - CLI boundary keeps credentials out of terminal errors
        # Detailed diagnostics stay in the administrator-only data directory.
        log = args.data_root / "logs/setup-errors.log"
        try:
            load_deployment(
                args.app_root,
                args.data_root,
                args.api_port,
                args.database_port,
                args.service_prefix,
            )
            owned = True
        except (OSError, ValueError, KeyError):
            owned = False
        if owned and log.parent.is_dir():
            with log.open("a", encoding="utf-8") as output:
                output.write(traceback.format_exc())
        print(
            "Initialization failed. Inspect the protected setup logs; existing data were preserved.",
            file=sys.stderr,
        )
        return 1
    return 0

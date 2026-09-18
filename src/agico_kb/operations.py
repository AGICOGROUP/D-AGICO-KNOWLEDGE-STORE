"""Logical backup, isolated restore, and read-only original-file reconciliation."""

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
from contextlib import contextmanager
from importlib.metadata import version
from pathlib import Path

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg.rows import dict_row

from .config import Settings
from .db import Database


@contextmanager
def pause_writes(settings, timeout=30):
    # Separate connection: no pool slot held while waiting for an existing writer.
    with psycopg.connect(
        settings.database_url,
        autocommit=True,
        connect_timeout=3,
        application_name="agico-backup:" + settings.schema,
    ) as conn:
        key = settings.schema + ":backup"
        conn.execute(
            "SELECT set_config('lock_timeout',%s,false)", (str(max(1, int(timeout * 1000))),)
        )
        try:
            conn.execute("SELECT pg_advisory_lock(hashtextextended(%s, 0))", (key,))
        except psycopg.errors.LockNotAvailable:
            raise TimeoutError("Timed out waiting for writes to finish") from None
        try:
            yield conn
        finally:
            conn.execute("SELECT pg_advisory_unlock(hashtextextended(%s, 0))", (key,))


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def safe_path(root, name):
    if not isinstance(name, str) or not name or "\\" in name or ":" in name:
        raise ValueError("Invalid artifact path")
    relative = Path(name)
    if relative.is_absolute() or any(p in {".", ".."} for p in relative.parts):
        raise ValueError("Invalid artifact path")
    root = root.resolve()
    target = root / relative
    if target.is_symlink() or not target.resolve().is_relative_to(root):
        raise ValueError("Artifact path escapes root")
    return target


def pg_tool(binary_dir, name, dsn, arguments, timeout=300):
    # libpq gets all connection information through child environment, never argv.
    env = os.environ.copy()
    for key in list(env):
        if key.startswith("PG"):
            del env[key]
    params = conninfo_to_dict(dsn)
    mapping = {
        "dbname": "PGDATABASE",
        "host": "PGHOST",
        "hostaddr": "PGHOSTADDR",
        "port": "PGPORT",
        "user": "PGUSER",
        "password": "PGPASSWORD",
        "sslmode": "PGSSLMODE",
        "sslrootcert": "PGSSLROOTCERT",
        "sslcert": "PGSSLCERT",
        "sslkey": "PGSSLKEY",
        "channel_binding": "PGCHANNELBINDING",
        "connect_timeout": "PGCONNECT_TIMEOUT",
        "options": "PGOPTIONS",
        "application_name": "PGAPPNAME",
        "passfile": "PGPASSFILE",
    }
    if set(params) - set(mapping):
        raise ValueError("Unsupported libpq connection option")
    env.update({mapping[k]: v for k, v in params.items()})
    env["PGCONNECT_TIMEOUT"] = "5"
    env["PATH"] = str(binary_dir.resolve()) + os.pathsep + env.get("PATH", "")
    binary = binary_dir / (name + (".exe" if os.name == "nt" else ""))
    try:
        result = subprocess.run(
            [str(binary.resolve()), *arguments],
            env=env,
            capture_output=True,
            check=False,
            timeout=timeout,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise RuntimeError(name + " unavailable or timed out") from None
    if result.returncode:
        raise RuntimeError(
            name + " failed; check tool versions and protected database configuration"
        )
    return result.stdout


def references(conn, schema):
    conn.execute(sql.SQL("SET search_path TO {}, public").format(sql.Identifier(schema)))
    return conn.execute(
        "SELECT blob_key AS path,size,sha256 FROM uploads WHERE state IN ('complete','submitted') ORDER BY blob_key"
    ).fetchall()


def check_blob(root, item):
    path = safe_path(root, item["path"])
    if not path.is_file() or path.stat().st_size != item["size"] or digest(path) != item["sha256"]:
        raise ValueError("Original file missing or checksum mismatch")
    return path


def reconcile(settings, stale_part_age_seconds=86400, timeout=30):
    """Read-only inventory while committed upload references cannot change."""
    if any(not math.isfinite(value) or value <= 0 for value in (stale_part_age_seconds, timeout)):
        raise ValueError("Reconciliation age and timeout must be finite and positive")
    report = {
        "checked_references": 0,
        "missing": [],
        "corrupt": [],
        "unreferenced_blobs": [],
        "stale_parts": [],
        "stale_part_age_seconds": stale_part_age_seconds,
        "scan_timeout_seconds": timeout,
        "warnings": [
            "Read-only report. Temporary uploads can still change; stale parts are review candidates, not confirmed abandoned files.",
            "Only application-managed writes are paused. External filesystem/database changes are not protected.",
        ],
    }
    with pause_writes(settings, timeout=min(timeout, 30)) as guard:
        started = time.perf_counter()
        cutoff = time.time() - stale_part_age_seconds
        guard.row_factory = dict_row
        guard.execute(
            "SELECT set_config('statement_timeout',%s,false)", (str(max(1, int(timeout * 1000))),)
        )

        def check_deadline():
            if time.perf_counter() - started >= timeout:
                raise TimeoutError("Reconciliation scan exceeded its time budget")

        rows = references(guard, settings.schema)
        referenced = {row["path"] for row in rows}
        for row in rows:
            check_deadline()
            path = safe_path(settings.storage_root, row["path"])
            if not path.exists():
                report["missing"].append(row["path"])
            elif not path.is_file() or path.stat().st_size != row["size"]:
                report["corrupt"].append(row["path"])
            else:
                checksum = hashlib.sha256()
                with path.open("rb") as stream:
                    for block in iter(lambda: stream.read(1024 * 1024), b""):
                        check_deadline()
                        checksum.update(block)
                if checksum.hexdigest() != row["sha256"]:
                    report["corrupt"].append(row["path"])
            report["checked_references"] += 1
        root = settings.storage_root
        if root.exists():
            for path in root.iterdir():
                check_deadline()
                if not re.fullmatch(r"[0-9a-f]{32}\.(blob|part)", path.name):
                    continue
                safe_path(root, path.name)
                try:
                    info = path.stat()
                except FileNotFoundError:
                    # Temporary streams may finish or disconnect while the write gate is held.
                    continue
                if path.suffix == ".blob" and path.name not in referenced:
                    report["unreferenced_blobs"].append(path.name)
                elif path.suffix == ".part" and info.st_mtime <= cutoff:
                    report["stale_parts"].append(path.name)
        check_deadline()
        # Never return a complete report if the original guard connection was lost.
        guard.execute("SELECT 1")
        report["pause_seconds"] = round(time.perf_counter() - started, 3)
    for key in ("missing", "corrupt", "unreferenced_blobs", "stale_parts"):
        report[key].sort()
    return report


def backup(settings, target, binary_dir, timeout=300):
    target = Path(target).resolve()
    if target.exists() or target.is_relative_to(settings.storage_root.resolve()):
        raise ValueError("Backup requires a new directory outside original storage")
    target.mkdir(parents=True)
    (target / "INCOMPLETE").write_text("Backup has not completed. Do not restore.\n")
    started = time.monotonic()
    with pause_writes(settings, timeout=min(timeout, 30)) as guard:
        with psycopg.connect(settings.database_url, row_factory=dict_row) as conn:
            blobs = references(conn, settings.schema)
            for item in blobs:
                source = check_blob(settings.storage_root, item)
                destination = safe_path(target / "blobs", item["path"])
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, destination)
                check_blob(target / "blobs", item)
            migrations = [
                r["version"]
                for r in conn.execute("SELECT version FROM schema_migrations ORDER BY version")
            ]
            taxonomy = {
                table: conn.execute(
                    sql.SQL("SELECT * FROM {} ORDER BY id").format(sql.Identifier(table))
                ).fetchall()
                for table in ("organizations", "categories")
            }
            models = [
                r["model_identity"]
                for r in conn.execute(
                    "SELECT DISTINCT model_identity FROM versions WHERE model_identity IS NOT NULL"
                )
            ]
        pg_tool(
            binary_dir,
            "pg_dump",
            settings.database_url,
            [
                "--format=custom",
                "--no-owner",
                "--no-acl",
                "--schema=" + settings.schema,
                "--file=" + str(target / "database.dump"),
            ],
            timeout,
        )
        lock = Path(__file__).resolve().parents[2] / "uv.lock"
        if not lock.is_file():
            raise ValueError("Dependency lockfile is required for recovery")
        shutil.copyfile(lock, target / "uv.lock")
        manifest = {
            "format": 1,
            "schema": settings.schema,
            "migrations": migrations,
            "blobs": blobs,
            "dump_sha256": digest(target / "database.dump"),
            "lock_sha256": digest(target / "uv.lock"),
            "taxonomy": taxonomy,
            "models": models,
            "application_version": version("agico-knowledge-store"),
            "embedding_model": settings.embedding_model,
            "model_recovery": "Provision model separately, pin expected identity, then run offline. Model cache is not embedded.",
            "backup_scope": "Logical database and committed originals. Same-disk copies are not independent disaster backups.",
        }
        manifest["pause_seconds"] = round(time.monotonic() - started, 3)
        # The original lock connection must still be live; never reconnect here.
        guard.execute("SELECT 1")
        (target / "manifest.json.tmp").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    (target / "INCOMPLETE").unlink()
    (target / "manifest.json.tmp").replace(target / "manifest.json")
    return manifest


def validate_backup(archive):
    if (archive / "INCOMPLETE").exists():
        raise ValueError("Incomplete backup")
    manifest = json.loads((archive / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("format") != 1 or not re.fullmatch(
        r"[a-zA-Z][a-zA-Z0-9_]{0,62}", manifest.get("schema", "")
    ):
        raise ValueError("Unsupported backup format or schema")
    if manifest["migrations"] not in ([1, 2], [1, 2, 3]):
        raise ValueError("Unsupported database migrations")
    for name, key in [("database.dump", "dump_sha256"), ("uv.lock", "lock_sha256")]:
        if digest(safe_path(archive, name)) != manifest[key]:
            raise ValueError("Backup checksum mismatch")
    names = set()
    for item in manifest["blobs"]:
        if item["path"] in names:
            raise ValueError("Duplicate original path")
        names.add(item["path"])
        check_blob(archive / "blobs", item)
    return manifest


def restore(archive, maintenance_dsn, database_name, storage, binary_dir, timeout=300):
    archive, storage = Path(archive).resolve(), Path(storage).resolve()
    if not re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_]{0,62}", database_name) or storage.exists():
        raise ValueError("Restore requires a new database and new storage directory")
    manifest = validate_backup(archive)
    with psycopg.connect(maintenance_dsn, autocommit=True, connect_timeout=3) as conn:
        if conn.execute("SELECT 1 FROM pg_database WHERE datname=%s", (database_name,)).fetchone():
            raise ValueError("Target database already exists")
        # No automatic deletion on failure. New isolated artifacts remain for inspection.
        storage.mkdir(parents=True)
        (storage / "RESTORE_INCOMPLETE").write_text("Isolated restore incomplete; do not serve.\n")
        conn.execute(
            sql.SQL("CREATE DATABASE {} TEMPLATE template0").format(sql.Identifier(database_name))
        )
    dsn = make_conninfo(maintenance_dsn, dbname=database_name)
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute("CREATE EXTENSION vector WITH SCHEMA public")
    # template0 already has public; retain its ownership and skip only CREATE SCHEMA public.
    toc = pg_tool(
        binary_dir, "pg_restore", dsn, ["--list", str(archive / "database.dump")], timeout
    ).decode("utf-8")
    toc_file = storage / "restore.list"
    toc_file.write_text(
        "\n".join(
            line
            for line in toc.splitlines()
            if not re.match(r"^\d+; \d+ \d+ SCHEMA - public ", line)
        ),
        encoding="utf-8",
    )
    pg_tool(
        binary_dir,
        "pg_restore",
        dsn,
        [
            "--dbname=" + database_name,
            "--use-list=" + str(toc_file),
            "--exit-on-error",
            "--no-owner",
            "--no-acl",
            str(archive / "database.dump"),
        ],
        timeout,
    )
    toc_file.unlink()
    for item in manifest["blobs"]:
        source = check_blob(archive / "blobs", item)
        destination = safe_path(storage, item["path"])
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        check_blob(storage, item)
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        rows = references(conn, manifest["schema"])
        if rows != manifest["blobs"]:
            raise ValueError("Restored database original references differ")
        conn.execute(
            "UPDATE jobs SET state='queued',generation=NULL,lease_until=NULL,attempts=0,next_attempt_at=now() WHERE state='running'"
        )
        conn.execute(
            "UPDATE versions SET processing_status='queued' WHERE active_generation IS NULL AND processing_status='processing' AND id IN (SELECT version_id FROM jobs WHERE state='queued')"
        )
    settings = Settings(
        dsn, storage, manifest["schema"], embedding_model=manifest["embedding_model"]
    )
    # Restore old archives into the current schema before the service can be started.
    restored_db = Database(settings)
    try:
        restored_db.migrate()
    finally:
        restored_db.close()
    (storage / "RESTORE_INCOMPLETE").unlink()
    return settings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["backup", "restore", "reconcile"])
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--pg-bin", type=Path)
    parser.add_argument("--database")
    parser.add_argument("--storage", type=Path)
    parser.add_argument("--stale-part-age-seconds", type=float, default=86400)
    parser.add_argument("--timeout", type=float, default=30)
    args = parser.parse_args()
    if args.action != "reconcile" and (not args.archive or not args.pg_bin):
        parser.error("backup and restore require --archive and --pg-bin")
    try:
        if args.action == "reconcile":
            report = reconcile(Settings.from_env(), args.stale_part_age_seconds, args.timeout)
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 0
        elif args.action == "backup":
            backup(Settings.from_env(), args.archive, args.pg_bin)
        else:
            if not args.database or not args.storage:
                parser.error("restore requires --database and --storage")
            restore(
                args.archive,
                os.environ["AGICO_KB_MAINTENANCE_URL"],
                args.database,
                args.storage,
                args.pg_bin,
            )
    except Exception:  # noqa: BLE001 - CLI must never render credential-bearing exception locals
        print(
            "Operation failed. No completion report produced; check protected configuration and artifact integrity.",
            file=sys.stderr,
        )
        return 1
    print("Operation completed. Validate recovered service before switching traffic.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

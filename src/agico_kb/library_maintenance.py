"""Backed-up, explicit document purge. Caller must stop API and worker first."""
import hashlib
import json
from pathlib import Path

import psycopg
from psycopg import sql

from . import operations
from .provider_config import no_links

# Exact scope: no identity, permission, OAuth, feedback, project or config table.
# Order follows foreign keys; document active-version pointers are cleared first.
CONTENT_TABLES = ('audit_events','document_grants','file_links','chunks','jobs','versions','documents','uploads')


def snapshot(conn, schema):
    """Counts and stable hashes only; never emit passwords, tokens or user rows."""
    tables = conn.execute('SELECT tablename FROM pg_tables WHERE schemaname=%s ORDER BY tablename', (schema,)).fetchall()
    result = {}
    for item in tables:
        name = item['tablename'] if isinstance(item,dict) else item[0]
        checksum, count = hashlib.sha256(), 0
        for row in conn.execute(sql.SQL('SELECT row_to_json(t)::text AS value FROM {} t ORDER BY row_to_json(t)::text').format(sql.Identifier(schema,name))):
            value = row['value'] if isinstance(row,dict) else row[0]
            checksum.update(value.encode('utf-8')+b'\n')
            count += 1
        result[name] = {'count':count,'sha256':checksum.hexdigest()}
    return result


def record(path, value):
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
    temp.replace(path)


def clear_library(settings, archive, binary_dir):
    """Keep a restorable logical backup and move originals into its protected parent.

    Files are renamed on the same filesystem, never recursively deleted. Any SQL
    error rolls the transaction back and returns moved files to the active root.
    A process/power interruption leaves the protected journal for recovery; the
    service orchestrator must not restart an unverified library.
    """
    root, archive = Path(settings.storage_root), Path(archive)
    no_links(root); no_links(archive)
    root, archive = root.resolve(), archive.resolve()
    if archive.exists() or archive.is_relative_to(root) or root.is_relative_to(archive):
        raise ValueError('A new backup directory outside original storage is required')
    if not root.is_dir() or root.stat().st_dev != archive.parent.stat().st_dev:
        raise ValueError('Original storage and backup must be existing local roots on the same volume')
    for item in root.rglob('*'):
        no_links(item)
    archive.mkdir()
    journal = archive/'clear-state.json'
    record(journal,{'stage':'backing_up'})
    with psycopg.connect(settings.database_url) as conn:
        before = snapshot(conn,settings.schema)
    if not set(CONTENT_TABLES).issubset(before):
        raise ValueError('Unsupported document schema')
    operations.backup(settings,archive/'database',binary_dir)
    operations.validate_backup(archive/'database')
    # Back up referenced originals and retain every remaining file/cache by rename.
    retired = archive/'retired-originals'
    retired.mkdir()
    moved = []
    try:
        with operations.pause_writes(settings) as guard:
            with guard.transaction():
                guard.execute("SET LOCAL statement_timeout='60s'")
                guard.execute(sql.SQL('LOCK TABLE {} IN ACCESS EXCLUSIVE MODE').format(
                    sql.SQL(',').join(sql.Identifier(settings.schema,name) for name in CONTENT_TABLES)))
                if snapshot(guard,settings.schema) != before:
                    raise ValueError('Database changed after backup; no purge was performed')
                record(journal,{'stage':'clearing','before':before})
                for source in root.iterdir():
                    no_links(source)
                    destination = retired/source.name
                    if not destination.resolve().is_relative_to(retired):
                        raise ValueError('Retired original path escapes backup')
                    source.rename(destination)
                    moved.append((source,destination))
                if any(root.iterdir()):
                    raise ValueError('Original storage is still being written; purge cancelled')
                guard.execute(sql.SQL('UPDATE {} SET active_version_id=NULL').format(sql.Identifier(settings.schema,'documents')))
                for name in CONTENT_TABLES:
                    guard.execute(sql.SQL('DELETE FROM {}').format(sql.Identifier(settings.schema,name)))
                after = snapshot(guard,settings.schema)
                if any(after[name]['count'] for name in CONTENT_TABLES):
                    raise ValueError('Document clear verification failed')
                if {k:v for k,v in before.items() if k not in CONTENT_TABLES} != {k:v for k,v in after.items() if k not in CONTENT_TABLES}:
                    raise ValueError('Protected rows changed; purge rolled back')
    except BaseException:
        for source,destination in reversed(moved):
            if destination.exists() and not source.exists():
                destination.rename(source)
        record(journal,{'stage':'not_cleared','before':before})
        raise
    result = {'stage':'complete','before':before,'after':after,'backup':str(archive)}
    record(journal,result)
    return result

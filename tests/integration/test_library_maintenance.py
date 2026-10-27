"""Only fresh synthetic documents; never a production database."""
from pathlib import Path
import importlib.util
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql

from conftest import submit
from agico_kb import library_maintenance as maintenance, operations
from agico_kb.accounts import bootstrap_superadmin


def test_clear_preserves_all_non_document_rows_and_has_restorable_backup(kb):
    client, db, root = kb
    for _ in range(2):
        response, _, _ = submit(client)
        assert response.status_code == 201
    bootstrap_superadmin(db, 'fresh-purge@example.test', uuid4().hex, must_change=False)
    with db.connection(write=True) as conn:
        before = maintenance.snapshot(conn, db.settings.schema)
        conn.execute("INSERT INTO projects(id,organization_id,code,status,name,visibility,created_by) VALUES (%s,'baiste',%s,'opportunity','合成项目','department','alice')", (uuid4(),uuid4().hex))
        before = maintenance.snapshot(conn, db.settings.schema)
    (root/'previews').mkdir()
    (root/'previews'/'synthetic.png').write_bytes(uuid4().bytes)
    originals = {p.relative_to(root).as_posix():operations.digest(p) for p in root.rglob('*') if p.is_file()}
    archive = root.parent/('fresh-clear-'+uuid4().hex)
    binary = Path('.local/postgres/Library/bin').resolve()
    result = maintenance.clear_library(db.settings, archive, binary)
    assert result['before']['documents']['count'] == 2
    assert not any(root.iterdir())
    with db.connection() as conn:
        after = maintenance.snapshot(conn, db.settings.schema)
    assert all(after[name]['count']==0 for name in maintenance.CONTENT_TABLES)
    assert {k:v for k,v in before.items() if k not in maintenance.CONTENT_TABLES} == {k:v for k,v in after.items() if k not in maintenance.CONTENT_TABLES}
    assert {p.relative_to(archive/'retired-originals').as_posix():operations.digest(p) for p in (archive/'retired-originals').rglob('*') if p.is_file()} == originals
    operations.validate_backup(archive/'database')
    restored_name = 'restore_test_'+uuid4().hex
    try:
        restored = operations.restore(archive/'database',db.settings.database_url,restored_name,root.parent/restored_name,binary)
        with psycopg.connect(restored.database_url) as conn:
            assert maintenance.snapshot(conn, db.settings.schema)['documents']['count'] == 2
    finally:
        with psycopg.connect(db.settings.database_url, autocommit=True) as conn:
            conn.execute(sql.SQL('DROP DATABASE IF EXISTS {} WITH (FORCE)').format(sql.Identifier(restored_name)))
    with pytest.raises(ValueError,match='new backup'):
        maintenance.clear_library(db.settings,archive,binary)


def test_failed_backup_never_clears_and_unknown_fk_rolls_back_files(kb, monkeypatch):
    client, db, root = kb
    assert submit(client)[0].status_code == 201
    original = {p.name:p.read_bytes() for p in root.iterdir() if p.is_file()}
    archive = root.parent/('fresh-clear-'+uuid4().hex)
    binary = Path('.local/postgres/Library/bin').resolve()
    real_backup = operations.backup
    def failed(*args,**kwargs):
        raise RuntimeError('synthetic backup failure')
    monkeypatch.setattr(operations,'backup',failed)
    with pytest.raises(RuntimeError,match='synthetic'):
        maintenance.clear_library(db.settings,archive,binary)
    assert {p.name:p.read_bytes() for p in root.iterdir() if p.is_file()} == original
    monkeypatch.setattr(operations,'backup',real_backup)
    with db.connection(write=True) as conn:
        conn.execute('CREATE TABLE unknown_document_use (document_id uuid REFERENCES documents(id))')
        conn.execute('INSERT INTO unknown_document_use SELECT id FROM documents')
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        maintenance.clear_library(db.settings,root.parent/('fresh-clear-'+uuid4().hex),binary)
    assert {p.name:p.read_bytes() for p in root.iterdir() if p.is_file()} == original
    with db.connection() as conn:
        assert maintenance.snapshot(conn,db.settings.schema)['documents']['count'] == 1


def test_delivered_adapter_clears_once_then_preserves_new_uploads(kb,monkeypatch):
    client,db,root=kb
    response,_,_=submit(client)
    assert response.status_code==201
    helper_path=Path('scripts/company-library-maintenance.py').resolve()
    spec=importlib.util.spec_from_file_location('fresh_company_adapter',helper_path)
    helper=importlib.util.module_from_spec(spec);spec.loader.exec_module(helper)
    # Substitute only deployment identity and executable location; all backup,
    # migration, deletion, receipt and verification code runs on the fresh schema.
    runtime={'AGICO_KB_DATABASE_URL':db.settings.database_url}
    monkeypatch.setattr(helper,'context',lambda *_:(db.settings,runtime,{'user':'agico_dev'},{}))
    monkeypatch.setattr(helper,'preflight',lambda *_:{'documents':1})
    real_pg_tool=operations.pg_tool
    binary=Path('.local/postgres/Library/bin').resolve()
    monkeypatch.setattr(operations,'pg_tool',lambda _,*args,**kw:real_pg_tool(binary,*args,**kw))
    data=root.parent/('synthetic-data-'+uuid4().hex)
    (data/'config').mkdir(parents=True)
    first=data/'first-backup';first.mkdir()
    helper.apply(Path.cwd(),data,first,True)
    import json
    assert json.loads((first/'result.json').read_text('utf-8'))['documents']=='cleared'
    assert submit(client)[0].status_code==201
    second=data/'second-backup';second.mkdir()
    helper.apply(Path.cwd(),data,second,True)
    result=json.loads((second/'result.json').read_text('utf-8'))
    assert result['documents']=='already_cleared_no_repeat'
    assert result['documents_after']==1

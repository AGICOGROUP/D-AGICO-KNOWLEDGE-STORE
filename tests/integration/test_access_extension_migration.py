import importlib.util
from pathlib import Path
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo


def test_company_migration_grants_new_table_to_existing_runtime_role(kb):
    _,db,_=kb
    path=Path('scripts/company-library-maintenance.py').resolve()
    spec=importlib.util.spec_from_file_location('company_maintenance',path)
    helper=importlib.util.module_from_spec(spec);spec.loader.exec_module(helper)
    role='test_extension_'+uuid4().hex
    password=uuid4().hex
    with db.connection(write=True) as conn:
        conn.execute('DROP TABLE account_access_extensions')
        conn.execute('DELETE FROM schema_migrations WHERE version=8')
        conn.execute(sql.SQL('CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD {}').format(sql.Identifier(role),sql.Literal(password)))
        conn.execute(sql.SQL('GRANT USAGE ON SCHEMA {} TO {}').format(sql.Identifier(db.settings.schema),sql.Identifier(role)))
    try:
        helper.migrate_access(db.settings,{'user':role})
        runtime=make_conninfo(db.settings.database_url,user=role,password=password)
        with psycopg.connect(runtime) as conn:
            conn.execute(sql.SQL('SELECT 1 FROM {} LIMIT 1').format(sql.Identifier(db.settings.schema,'account_access_extensions')))
            assert conn.execute('SELECT has_table_privilege(%s,%s)',(db.settings.schema+'.account_access_extensions','INSERT')).fetchone()[0]
            assert not conn.execute('SELECT has_table_privilege(%s,%s)',(db.settings.schema+'.account_users','UPDATE')).fetchone()[0]
        helper.migrate_access(db.settings,{'user':role})
    finally:
        with db.connection(write=True) as conn:
            conn.execute(sql.SQL('DROP OWNED BY {}').format(sql.Identifier(role)))
            conn.execute(sql.SQL('DROP ROLE {}').format(sql.Identifier(role)))

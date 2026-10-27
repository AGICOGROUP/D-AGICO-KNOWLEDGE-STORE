"""Company deployment adapter. No credentials or account rows in console output."""
import argparse
import json
from pathlib import Path
import sys
import traceback

import httpx
import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from agico_kb.config import Settings
from agico_kb import operations
from agico_kb.provider_config import no_links

HOST = 'https://zsk.agicogroup.com:8888'
DATABASE = 'agico_company_20261026_9aaf8778'


def read(path):
    no_links(path)
    return json.loads(path.read_text(encoding='utf-8-sig'))


def context(app_root,data_root):
    no_links(app_root);no_links(data_root)
    app_root,data_root=app_root.resolve(),data_root.resolve()
    marker=read(data_root/'config/deployment.json')
    if Path(marker['app_root']).resolve()!=app_root or Path(marker['data_root']).resolve()!=data_root or marker['stage']!='installed':
        raise ValueError('Deployment identity mismatch')
    runtime=read(data_root/'config/runtime.json')
    if runtime.get('AGICO_KB_PUBLIC_URL','').rstrip('/')!=HOST or Path(runtime['AGICO_KB_STORAGE_ROOT']).resolve()!=data_root/'originals':
        raise ValueError('Company URL or storage mismatch')
    if Path(runtime['AGICO_KB_DINGTALK_CONFIG_FILE']).resolve()!=data_root/'config/dingtalk.json':
        raise ValueError('Company identity configuration mismatch')
    params=conninfo_to_dict(runtime['AGICO_KB_DATABASE_URL'])
    if params['dbname']!=DATABASE or params.get('host')!='127.0.0.1' or int(params.get('port',0))!=int(marker['database_port']):
        raise ValueError('Company database mismatch')
    for name in ('api.json','worker.json'):
        child=read(data_root/'config'/name)
        if child['AGICO_KB_DATABASE_URL']!=runtime['AGICO_KB_DATABASE_URL'] or child['AGICO_KB_STORAGE_ROOT']!=runtime['AGICO_KB_STORAGE_ROOT']:
            raise ValueError('Runtime services do not use the same database and storage')
    maintenance=read(data_root/'config/maintenance.json')['database_url']
    owner=make_conninfo(maintenance,dbname=params['dbname'])
    settings=Settings(owner,data_root/'originals',runtime.get('AGICO_KB_SCHEMA','public'))
    return settings,runtime,params,marker


def preflight(settings,params):
    with psycopg.connect(settings.database_url) as conn:
        versions=[r[0] for r in conn.execute(sql.SQL('SELECT version FROM {} ORDER BY version').format(sql.Identifier(settings.schema,'schema_migrations')))]
        if versions not in ([1,2,3,4,5,6,7],[1,2,3,4,5,6,7,8]):
            raise ValueError('Unrecognized company schema')
        counts={}
        for table in ('documents','uploads','account_users','dingtalk_identities'):
            counts[table]=conn.execute(sql.SQL('SELECT count(*) FROM {}').format(sql.Identifier(settings.schema,table))).fetchone()[0]
        if counts['account_users']<1 or counts['dingtalk_identities']<1:
            raise ValueError('Migrated company identities are required')
        role=conn.execute('SELECT rolsuper,rolcreatedb,rolcreaterole FROM pg_roles WHERE rolname=%s',(params['user'],)).fetchone()
        if role!=(False,False,False):
            raise ValueError('Unexpected runtime database role')
    return counts


def migrate_access(settings,params):
    with psycopg.connect(settings.database_url) as conn:
        conn.execute(sql.SQL('SET LOCAL search_path TO {}, public').format(sql.Identifier(settings.schema)))
        conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(current_schema() || ':migrate',0))")
        if not conn.execute('SELECT 1 FROM schema_migrations WHERE version=8').fetchone():
            migration=Path(__file__).resolve().parent.parent/'src/agico_kb/migrations/008_access_extensions.sql'
            conn.execute(migration.read_text('utf-8'))
            conn.execute('INSERT INTO schema_migrations VALUES (8)')
        conn.execute(sql.SQL('GRANT SELECT,INSERT,UPDATE,DELETE ON {} TO {}').format(
            sql.Identifier(settings.schema,'account_access_extensions'),sql.Identifier(params['user'])))


def apply(app_root,data_root,archive,clear_documents):
    from agico_kb.library_maintenance import clear_library,record,snapshot,CONTENT_TABLES
    settings,runtime,params,marker=context(app_root,data_root)
    counts=preflight(settings,params)
    binary=app_root/'.runtime/postgres/Library/bin'
    print('BACKING_UP_CURRENT_DATABASE_AND_ORIGINALS',flush=True)
    operations.backup(settings,archive/'pre-update-database',binary)
    operations.validate_backup(archive/'pre-update-database')
    with psycopg.connect(settings.database_url) as conn:
        before=snapshot(conn,settings.schema)
    migrate_access(settings,params)
    with psycopg.connect(settings.database_url) as conn:
        after=snapshot(conn,settings.schema)
    if (any(after.get(k)!=v for k,v in before.items() if k!='schema_migrations') or
            set(after)-set(before)-{'account_access_extensions'}):
        # New table is the only permissible addition. Existing extension rows
        # must also remain unchanged when an update is run a second time.
        raise ValueError('Protected database rows changed during migration')
    status='not_requested'
    receipt=data_root/'config/library-clear-20261027.json'
    if clear_documents:
        if receipt.exists():
            previous=read(receipt)
            if previous.get('stage')!='complete' or previous.get('database')!=DATABASE or previous.get('schema')!=settings.schema:
                raise ValueError('Existing clear receipt requires inspection')
            status='already_cleared_no_repeat'
            print('CLEAR_ALREADY_COMPLETED: new documents will be preserved',flush=True)
        else:
            print('CLEARING_DOCUMENTS_WITH_VERIFIED_BACKUP',flush=True)
            result=clear_library(settings,archive/'library-clear',binary)
            record(receipt,{'stage':'complete','database':DATABASE,'schema':settings.schema,'backup':result['backup'],
                            'documents_before':result['before']['documents']['count']})
            status='cleared'
    # Verify the API database account can read the new table; no user/key mutation.
    with psycopg.connect(runtime['AGICO_KB_DATABASE_URL']) as conn:
        conn.execute(sql.SQL('SELECT 1 FROM {} LIMIT 1').format(sql.Identifier(settings.schema,'account_access_extensions')))
        final=snapshot(conn,settings.schema)
    if status=='cleared' and (any(final[name]['count'] for name in CONTENT_TABLES) or any(settings.storage_root.iterdir())):
        raise ValueError('Document clear did not remain empty')
    result={'stage':'complete','access_migration':8,'documents':status,'counts_before':counts,
            'documents_after':final['documents']['count'],'backup':str(archive)}
    record(archive/'result.json',result)
    print(json.dumps(result,ensure_ascii=False),flush=True)


def verify(app_root,data_root):
    settings,runtime,params,marker=context(app_root,data_root)
    base='http://127.0.0.1:'+str(marker['api_port'])
    with httpx.Client(trust_env=False,timeout=5) as client:
        ready=client.get(base+'/health/ready')
        ready.raise_for_status()
        if ready.json().get('status')!='ready':raise ValueError('Backend not ready')
        auth=client.get(base+'/v1/auth/settings')
        if auth.json().get('auth_mode')!='dingtalk':raise ValueError('DingTalk authentication changed')
        if client.get(base+'/v1/documents').status_code!=401:raise ValueError('Anonymous document access changed')
        asset=client.get(base+'/portal/admin.js')
        if asset.status_code!=200 or 'extra_organizations' not in asset.text:raise ValueError('Personnel editor was not updated')
    print('COMPANY_LIBRARY_UPDATE_VERIFIED',flush=True)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('action',choices=['preflight','apply','verify'])
    parser.add_argument('--app-root',type=Path,required=True)
    parser.add_argument('--data-root',type=Path,required=True)
    parser.add_argument('--archive',type=Path)
    parser.add_argument('--clear-documents',action='store_true')
    args=parser.parse_args()
    try:
        if args.action=='preflight':
            settings,_,params,_=context(args.app_root,args.data_root)
            print(json.dumps({'preflight':preflight(settings,params)}))
        elif args.action=='verify':verify(args.app_root,args.data_root)
        else:
            if not args.archive or not args.archive.is_dir():raise ValueError('Protected archive directory required')
            apply(args.app_root,args.data_root,args.archive,args.clear_documents)
        return 0
    except Exception as exc:
        if args.archive and args.archive.is_dir():
            (args.archive/'maintenance-error.txt').write_text(traceback.format_exc(),encoding='utf-8')
        print('COMPANY_LIBRARY_UPDATE_FAILED: '+type(exc).__name__+'. See protected maintenance-error.txt.',file=sys.stderr)
        return 1


if __name__=='__main__':
    raise SystemExit(main())

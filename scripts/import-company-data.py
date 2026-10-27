"""Import a verified DingTalk instance into a new company database, never overwrite data."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
from datetime import date, datetime, timezone

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from agico_kb.dingtalk_client import DingTalkClient, DingTalkConfig
from agico_kb.operations import restore, validate_backup
from agico_kb.provider_config import RUNTIME_KEYS, no_links, validate_bundle
from agico_kb.server_setup import _replace_json
from agico_kb.vision import _credential_models


def json_read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def row_fingerprint(rows):
    def normalize(value):
        if isinstance(value,datetime):
            if value.tzinfo is not None:value=value.astimezone(timezone.utc)
            return value.isoformat(timespec='microseconds')
        if isinstance(value,date):return value.isoformat()
        if isinstance(value,(bytes,bytearray,memoryview)):return bytes(value).hex()
        if isinstance(value,(list,tuple)):return [normalize(v) for v in value]
        if isinstance(value,dict):return {str(k):normalize(v) for k,v in value.items()}
        if value is None or isinstance(value,(str,int,float,bool)):return value
        return str(value)
    values=sorted(json.dumps(normalize(row),ensure_ascii=False,separators=(',',':'),sort_keys=True) for row in rows)
    return hashlib.sha256('\n'.join(values).encode()).hexdigest()


def validate_payload(bundle):
    bundle=Path(bundle).resolve();no_links(bundle)
    manifest=json_read(bundle/'manifest.json')
    if manifest.get('format')!=1 or not re.fullmatch(r'20261026_[a-f0-9]{8}',manifest.get('migration_id','')):
        raise ValueError('Invalid migration manifest')
    files=manifest.get('files')
    if not isinstance(files,dict) or not files:raise ValueError('Empty migration manifest')
    required={'database/manifest.json','database/database.dump','database/uv.lock','config/dingtalk.json','source-invariants.json','runtime-patch.json'}
    if not required.issubset(files):raise ValueError('Incomplete migration manifest')
    for name,expected in files.items():
        if not isinstance(name,str) or '\\' in name or ':' in name or Path(name).is_absolute() or '..' in Path(name).parts:
            raise ValueError('Unsafe migration file path')
        path=bundle/name;no_links(path)
        if not path.resolve().is_relative_to(bundle) or not path.is_file():raise ValueError('Migration file missing')
        with path.open('rb') as stream:actual=hashlib.file_digest(stream,'sha256').hexdigest()
        if actual!=expected:raise ValueError('Migration file checksum failed')
    actual_files={p.relative_to(bundle).as_posix() for p in bundle.rglob('*') if p.is_file()}
    if actual_files != set(files)|{'manifest.json'}:raise ValueError('Unlisted or missing migration files')
    allowed_config={'config/dingtalk.json','config/.api-key-encryption.key','config/api-keys/primary.txt','config/api-keys/fallback.txt','config/api-keys/models.json'}
    if any(name.startswith('config/') and name not in allowed_config for name in files):raise ValueError('Unexpected private configuration file')
    validate_backup(bundle/'database')
    DingTalkConfig.read(bundle/'config/dingtalk.json')
    patch=json_read(bundle/'runtime-patch.json')
    if set(patch)-RUNTIME_KEYS or any(not isinstance(v,str) for v in patch.values()):raise ValueError('Unsafe runtime patch')
    profiles={}
    for profile in ('primary','fallback'):
        path=bundle/'config/api-keys'/(profile+'.txt')
        if path.exists():
            key,models=_credential_models(path)
            if not key or not models:raise ValueError('Provider credential could not be recovered')
            profiles[profile]=list(models)
    validate_bundle({'version':2,'runtime':patch,'profiles':profiles})
    return manifest


def context(app_root,data_root,bundle):
    app_root=Path(app_root).resolve();data_root=Path(data_root).resolve()
    no_links(app_root);no_links(data_root)
    marker=json_read(data_root/'config/deployment.json')
    if Path(marker['app_root']).resolve()!=app_root or Path(marker['data_root']).resolve()!=data_root or marker['stage']!='installed':
        raise ValueError('Target deployment identity mismatch')
    https=json_read(data_root/'config/https/deployment.json')
    if https['mode']!='maintenance' or https['hostname']!='zsk.agicogroup.com' or https['port']!=8888:
        raise ValueError('Target HTTPS must remain in the approved maintenance mode')
    manifest=validate_payload(bundle)
    runtime=json_read(data_root/'config/runtime.json')
    expected=json_read(bundle/'source-invariants.json')
    if runtime.get('AGICO_KB_EXPECTED_MODEL_IDENTITY')!=expected['model_identity']:
        raise ValueError('Source and target embedding model pins differ')
    if Path(runtime['AGICO_KB_STORAGE_ROOT']).resolve()!=data_root/'originals':raise ValueError('Unexpected target original storage')
    if Path(runtime['AGICO_KB_MODEL_CACHE']).resolve()!=data_root/'models':raise ValueError('Unexpected target model cache')
    journal=data_root/'config'/('migration-'+manifest['migration_id']+'.json')
    no_links(journal)
    return app_root,data_root,marker,manifest,runtime,expected,journal


def verify_rows(dsn,expected):
    with psycopg.connect(dsn,connect_timeout=5) as conn:
        for table,digest in expected['fingerprints'].items():
            if table not in {'account_users','principals','memberships','dingtalk_identities','documents','versions','uploads'}:
                raise ValueError('Unexpected invariant table')
            rows=conn.execute(sql.SQL('SELECT * FROM {}').format(sql.Identifier(table))).fetchall()
            if row_fingerprint(rows)!=digest:raise ValueError('Restored invariant mismatch: '+table)


def check_empty(runtime,data_root):
    with psycopg.connect(runtime['AGICO_KB_DATABASE_URL'],connect_timeout=5) as conn:
        for table in ('account_users','documents','uploads','dingtalk_identities'):
            if conn.execute(sql.SQL('SELECT count(*) FROM {}').format(sql.Identifier(table))).fetchone()[0]:
                raise ValueError('Target contains live data; refusing overwrite')
    originals=data_root/'originals';no_links(originals)
    if not originals.is_dir() or any(originals.iterdir()):raise ValueError('Target original directory is not empty')
    for path in (data_root/'config/dingtalk.json',data_root/'config/.api-key-encryption.key'):
        no_links(path)
        if path.exists():raise ValueError('Target already has private identity or encryption configuration')
    key_dir=data_root/'config/api-keys';no_links(key_dir)
    if key_dir.exists() and any(key_dir.glob('*.txt')):raise ValueError('Target already has provider keys')


def import_instance(app_root,data_root,bundle,*,check_only=False,live_probe=True,pg_bin=None):
    bundle=Path(bundle).resolve()
    app_root,data_root,marker,manifest,runtime,expected,journal=context(app_root,data_root,bundle)
    migration_id=manifest['migration_id'];database='agico_company_'+migration_id
    previous=json_read(journal) if journal.exists() else None
    if previous and (previous['migration_id']!=migration_id or previous['database']!=database):raise ValueError('Migration journal mismatch')
    if previous and previous['stage']=='complete':
        if conninfo_to_dict(runtime['AGICO_KB_DATABASE_URL'])['dbname']!=database:raise ValueError('Completed migration no longer matches runtime')
        return {'status':'already_imported','documents':expected['counts']['documents'],'dingtalk_identities':expected['counts']['dingtalk_identities']}
    if not previous:check_empty(runtime,data_root)
    if check_only:return {'status':'preflight_passed','documents':expected['counts']['documents']}
    if previous and previous['stage']=='restored' and not (data_root/('migration-work-'+migration_id)/'originals').is_dir():
        raise ValueError('Partial migration needs inspection; no destructive retry performed')
    maintenance=json_read(data_root/'config/maintenance.json')['database_url']
    target_dsn=make_conninfo(runtime['AGICO_KB_DATABASE_URL'],dbname=database)
    owner_dsn=make_conninfo(maintenance,dbname=database)
    working=data_root/('migration-work-'+migration_id)
    no_links(working)
    if not previous:
        working.mkdir(exist_ok=False)
        config_backup=working/'config-before';config_backup.mkdir()
        for name in ('runtime.json','api.json','worker.json'):
            path=data_root/'config'/name
            if path.exists():shutil.copyfile(path,config_backup/name)
        if (data_root/'config/api-keys').exists():shutil.copytree(data_root/'config/api-keys',config_backup/'api-keys')
        role=conninfo_to_dict(runtime['AGICO_KB_DATABASE_URL'])['user']
        restored=restore(bundle/'database',maintenance,database,working/'originals',Path(pg_bin) if pg_bin else app_root/'.runtime/postgres/Library/bin',runtime_role=role)
        verify_rows(restored.database_url,expected)
        _replace_json(journal,{'migration_id':migration_id,'database':database,'stage':'restored'})
    else:verify_rows(owner_dsn,expected)
    if live_probe:
        client=DingTalkClient(DingTalkConfig.read(bundle/'config/dingtalk.json'))
        try:
            with psycopg.connect(owner_dsn) as conn:row=conn.execute('SELECT user_id FROM dingtalk_identities ORDER BY principal_id LIMIT 1').fetchone()
            if row:
                employee=client.employee(row[0])
                if not employee.active or not client.visible(employee,[]):raise ValueError('Existing DingTalk employee cannot use the app')
        finally:client.close()
    # Everything above is verified before switching any active path or runtime.
    old_originals=data_root/'originals'
    if any(old_originals.iterdir()):raise ValueError('Original target storage changed during migration')
    if (working/'originals-before').exists():raise ValueError('Original directory swap needs inspection')
    for path in (old_originals,working/'originals',working/'originals-before'):
        no_links(path)
        if not path.resolve().is_relative_to(data_root):raise ValueError('Storage swap escapes data directory')
    old_originals.rename(working/'originals-before')
    (working/'originals').rename(old_originals)
    config=data_root/'config'
    for source in (bundle/'config').rglob('*'):
        if not source.is_file():continue
        destination=config/source.relative_to(bundle/'config');no_links(destination)
        if destination.exists():
            allowed=(destination==config/'api-keys/models.json')
            if not allowed:raise ValueError('Existing target private file preserved; migration needs inspection')
        destination.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(source,destination)
    runtime.update(json_read(bundle/'runtime-patch.json'))
    runtime['AGICO_KB_DATABASE_URL']=target_dsn
    runtime['AGICO_KB_DINGTALK_CONFIG_FILE']=str(config/'dingtalk.json')
    runtime['AGICO_KB_API_KEYS_DIR']=str(config/'api-keys')
    for profile,key in (('primary','AGICO_KB_VISION_CONFIG_FILE'),('fallback','AGICO_KB_VISION_FALLBACK_CONFIG_FILE')):
        profile_path=config/'api-keys'/(profile+'.txt')
        if profile_path.exists():runtime[key]=str(profile_path)
        else:runtime.pop(key,None)
    runtime['AGICO_KB_PUBLIC_URL']='https://zsk.agicogroup.com:8888'
    hosts=[x for x in runtime.get('AGICO_KB_ALLOWED_HOSTS','127.0.0.1:*,localhost:*').split(',') if x]
    runtime['AGICO_KB_ALLOWED_HOSTS']=','.join(dict.fromkeys(hosts+['zsk.agicogroup.com:8888']))
    _replace_json(config/'runtime.json',runtime)
    verify_rows(target_dsn,expected)
    for profile in ('primary','fallback'):
        path=config/'api-keys'/(profile+'.txt')
        if path.exists():
            secret,models=_credential_models(path)
            if not secret:raise ValueError('Restored provider key did not decrypt')
    _replace_json(journal,{'migration_id':migration_id,'database':database,'stage':'complete','completed_utc':datetime.now(timezone.utc).isoformat(),'counts':expected['counts']})
    return {'status':'imported','documents':expected['counts']['documents'],'dingtalk_identities':expected['counts']['dingtalk_identities'],'department_mappings':len(DingTalkConfig.read(config/'dingtalk.json').department_map),'existing_target_database_preserved':True,'https_mode':'maintenance'}


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--app-root',type=Path,required=True);parser.add_argument('--data-root',type=Path,required=True);parser.add_argument('--bundle',type=Path,required=True);parser.add_argument('--check-only',action='store_true')
    args=parser.parse_args()
    try:
        print(json.dumps(import_instance(args.app_root,args.data_root,args.bundle,check_only=args.check_only),ensure_ascii=True))
    except Exception as error:
        # No traceback or exception locals: imported configs contain real credentials.
        detail=str(error) if isinstance(error,ValueError) else str(getattr(error,'code',''))
        print('Migration failed: '+type(error).__name__+': '+detail+'. Data retained; HTTPS remains in maintenance mode.')
        return 1
    return 0

if __name__=='__main__':raise SystemExit(main())

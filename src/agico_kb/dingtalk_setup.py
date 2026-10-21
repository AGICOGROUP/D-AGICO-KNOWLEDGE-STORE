"""Operator-only private configuration import and read-only connection probe."""
import argparse
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from .dingtalk_client import DingTalkClient, DingTalkConfig
from .provider_config import no_links


def protect(path, *, directory=False):
    if os.name == 'nt':
        suffix = '(OI)(CI)(F)' if directory else '(F)'
        result = subprocess.run(['icacls.exe', str(path), '/inheritance:r', '/grant:r',
            '*S-1-5-18:' + suffix, '*S-1-5-32-544:' + suffix],
            capture_output=True, timeout=15)
        if result.returncode:
            raise ValueError('Unable to protect DingTalk private configuration; run as administrator')
    else:
        path.chmod(0o700 if directory else 0o600)


def import_from_cli(app_id, corp_id, target, *, agent_id=None):
    executable = shutil.which('dws.cmd' if os.name == 'nt' else 'dws')
    if not executable:
        raise ValueError('DingTalk CLI not installed')
    result = subprocess.run([executable, 'dev', 'app', 'credentials', 'get',
        '--unified-app-id', app_id, '--format', 'json'], capture_output=True,
        text=True, encoding='utf-8', timeout=30)
    try:
        response = json.loads(result.stdout)
        data = response['data']
        if result.returncode or not response.get('ok'):
            raise ValueError()
        key = data.get('clientId') or data.get('appKey')
        secret = data.get('clientSecret') or data.get('appSecret')
        agent = data.get('agentId') or agent_id
        if not isinstance(key, str) or not key or not isinstance(secret, str) or not secret or not agent:
            raise ValueError()
    except (ValueError, KeyError, TypeError):
        raise ValueError('Application credentials unavailable; activate the app and check developer permissions') from None
    target = Path(target).absolute()
    no_links(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    protect(target.parent, directory=True)
    if target.exists():
        raise ValueError('Private configuration already exists; validate it or replace it explicitly through protected administration')
    content = dict(corp_id=corp_id, app_key=key, app_secret=secret, agent_id=str(agent),
                   department_map={}, maintenance_principals=[], refresh_seconds=300)
    handle, temporary = tempfile.mkstemp(prefix='.dingtalk-', dir=target.parent)
    os.close(handle)
    temporary = Path(temporary)
    try:
        protect(temporary)
        temporary.write_text(json.dumps(content, ensure_ascii=False, indent=2), encoding='utf-8')
        DingTalkConfig.read(temporary)
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    return {'corp_id': corp_id, 'app_key': key, 'agent_id': str(agent), 'private_configuration_saved': True}


def install_private_config(app_root, data_root):
    source = Path(app_root) / '.deployment-private/dingtalk.json'
    if not source.exists():
        return False
    no_links(source)
    DingTalkConfig.read(source)
    target = Path(data_root) / 'config/dingtalk.json'
    no_links(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    content = source.read_bytes()
    if target.exists():
        if target.read_bytes() != content:
            raise ValueError('Server DingTalk configuration differs; refusing to overwrite identity mappings')
        return True
    handle, temporary = tempfile.mkstemp(prefix='.dingtalk-', dir=target.parent)
    os.close(handle)
    temporary = Path(temporary)
    try:
        protect(temporary)
        temporary.write_bytes(content)
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    # Explicit activation follows migration/binding checks; no database/domain fields changed.
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['import-cli', 'validate', 'probe'])
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--app-id')
    parser.add_argument('--corp-id')
    parser.add_argument('--agent-id', help='Verified AgentId from this application page, if CLI omits it')
    args = parser.parse_args()
    try:
        if args.action == 'import-cli':
            if not args.app_id or not args.corp_id:
                raise ValueError('Application and company identifiers required')
            result = import_from_cli(args.app_id, args.corp_id, args.config, agent_id=args.agent_id)
        else:
            config = DingTalkConfig.read(args.config)
            result = {'corp_id': config.corp_id, 'configured': True, 'mapped_departments': len(config.department_map)}
            if args.action == 'probe':
                client = DingTalkClient(config)
                try:
                    result['departments'] = client.departments()
                    result['provider_read_verified'] = True
                    result['client_login_verified'] = False
                finally:
                    client.close()
        print(json.dumps(result, ensure_ascii=False))
    except Exception as exc:
        # Never render upstream payloads, CLI stderr, credentials or traceback.
        from .errors import KBError
        message = exc.payload['error']['message'] if isinstance(exc, KBError) else str(exc) if isinstance(exc, ValueError) else type(exc).__name__
        print(json.dumps({'ok': False, 'message': message}, ensure_ascii=False))
        raise SystemExit(1) from None


if __name__ == '__main__':
    main()

import json
import subprocess

import pytest

from agico_kb.dingtalk_setup import import_from_cli, install_private_config


def test_cli_import_keeps_secret_in_protected_private_file_only(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr('agico_kb.dingtalk_setup.shutil.which', lambda _: 'dws-fixture')
    def run(args, **kwargs):
        if 'credentials' in args:
            return subprocess.CompletedProcess(args, 0, json.dumps({'ok': True, 'data': {
                'appKey': 'test-key', 'appSecret': 'fresh-synthetic-secret', 'agentId': 123}}), '')
        return subprocess.CompletedProcess(args, 0, '', '')
    monkeypatch.setattr('agico_kb.dingtalk_setup.subprocess.run', run)
    path = tmp_path / 'private' / 'dingtalk.json'
    result = import_from_cli('fixture-id', 'ding-test', path)
    assert json.loads(path.read_text())['app_secret'] == 'fresh-synthetic-secret'
    assert result['corp_id'] == 'ding-test'
    assert 'fresh-synthetic-secret' not in capsys.readouterr().out
    assert 'app_secret' not in result


def test_missing_secret_never_creates_incomplete_configuration(tmp_path, monkeypatch):
    monkeypatch.setattr('agico_kb.dingtalk_setup.shutil.which', lambda _: 'dws-fixture')
    monkeypatch.setattr('agico_kb.dingtalk_setup.subprocess.run', lambda *a, **kw:
        subprocess.CompletedProcess(a[0], 0, json.dumps({'ok': True, 'data': {'appKey': 'key'}}), ''))
    path = tmp_path / 'private' / 'dingtalk.json'
    with pytest.raises(ValueError):
        import_from_cli('fixture-id', 'ding-test', path)
    assert not path.exists()


def test_cli_without_agent_id_accepts_verified_application_page_id(tmp_path, monkeypatch):
    monkeypatch.setattr('agico_kb.dingtalk_setup.shutil.which', lambda _: 'dws-fixture')
    def run(args, **kwargs):
        return subprocess.CompletedProcess(args, 0, json.dumps({'ok': True, 'data': {
            'appKey': 'key', 'appSecret': 'fresh-secret'}}) if 'credentials' in args else '', '')
    monkeypatch.setattr('agico_kb.dingtalk_setup.subprocess.run', run)
    result = import_from_cli('app', 'corp', tmp_path / 'private' / 'config.json', agent_id='123')
    assert result['agent_id'] == '123'


def test_portable_configuration_preserves_target_database_and_does_not_enable_login(tmp_path, monkeypatch):
    app, data = tmp_path / 'app', tmp_path / 'data'
    (app / '.deployment-private').mkdir(parents=True)
    (data / 'config').mkdir(parents=True)
    value = dict(corp_id='corp', app_key='key', app_secret='fresh-test-only', agent_id='123', department_map={})
    (app / '.deployment-private/dingtalk.json').write_text(json.dumps(value))
    runtime = data / 'config/runtime.json'
    runtime.write_text('{"AGICO_KB_DATABASE_URL":"preserved-target-only"}')
    monkeypatch.setattr('agico_kb.dingtalk_setup.protect', lambda *a, **kw: None)
    assert install_private_config(app, data)
    assert json.loads((data / 'config/dingtalk.json').read_text()) == value
    assert json.loads(runtime.read_text()) == {'AGICO_KB_DATABASE_URL': 'preserved-target-only'}

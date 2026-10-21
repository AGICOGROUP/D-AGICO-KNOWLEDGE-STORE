import json

import pytest

from agico_kb.provider_config import export_providers, import_providers


def source_runtime(tmp_path):
    folder=tmp_path/'old-config'; folder.mkdir()
    primary=folder/'primary.txt'; fallback=folder/'fallback.txt'
    primary.write_text('api=fresh-primary-test-secret\nmodel=glm-5.3-flash')
    fallback.write_text('api=fresh-fallback-test-secret\nmodel=glm-5.3-flashx/glm-5.3-flash')
    runtime=folder/'runtime.json'
    runtime.write_text(json.dumps({'AGICO_KB_DATABASE_URL':'source-db-secret',
        'AGICO_KB_STORAGE_ROOT':'source-originals','AGICO_KB_PUBLIC_URL':'https://old.example.test',
        'AGICO_KB_VISION_CONFIG_FILE':str(primary),'AGICO_KB_VISION_FALLBACK_CONFIG_FILE':str(fallback),
        'AGICO_KB_VISION_API_URL':'https://open.bigmodel.cn/api/paas/v4/chat/completions',
        'AGICO_KB_VISION_FALLBACK_API_URL':'https://cephalon.cloud/user-center/v1/model/chat/completions',
        'AGICO_KB_VISION_FALLBACK_MODEL':'glm-5.3-flashx','AGICO_KB_VISION_PDF_MODEL':'glm-5.3-flash',
        'AGICO_KB_VISION_MAX_CALLS_PER_DAY':'0',
        'AGICO_KB_NAMING_FALLBACK_MODELS':'glm-5.3-flashx,glm-5.3-flash,deepseek-v4-flash'}))
    return runtime


def test_configuration_moves_without_any_keys_or_source_paths(tmp_path):
    runtime=source_runtime(tmp_path)
    app=tmp_path/'new-application'; app.mkdir()
    private=app/'.deployment-private/providers.json'
    export_providers(runtime,private)
    text=private.read_text()
    assert 'source-db-secret' not in text and 'source-originals' not in text
    assert 'old.example.test' not in text and str(runtime.parent) not in text
    assert 'fresh-primary-test-secret' not in text and 'fresh-fallback-test-secret' not in text
    target=tmp_path/'server-data'; (target/'config').mkdir(parents=True)
    target_runtime=target/'config/runtime.json'
    original={'AGICO_KB_DATABASE_URL':'server-password-preserved','AGICO_KB_SCHEMA':'formal_accounts',
              'AGICO_KB_STORAGE_ROOT':str(target/'originals'),'AGICO_KB_PUBLIC_URL':'https://new.example.test'}
    target_runtime.write_text(json.dumps(original))
    assert import_providers(app,target)
    loaded=json.loads(target_runtime.read_text())
    assert all(loaded[name]==value for name,value in original.items())
    assert loaded['AGICO_KB_NAMING_FALLBACK_MODELS'].endswith('deepseek-v4-flash')
    assert 'AGICO_KB_VISION_CONFIG_FILE' not in loaded
    assert 'AGICO_KB_VISION_FALLBACK_CONFIG_FILE' not in loaded
    from pathlib import Path
    folder = Path(loaded['AGICO_KB_API_KEYS_DIR'])
    assert folder == target/'config/api-keys'
    assert not (folder/'primary.txt').exists()
    assert json.loads((folder/'models.json').read_text())['fallback'] == ['glm-5.3-flashx','glm-5.3-flash']
    snapshot=target_runtime.read_text()
    assert import_providers(app,target)
    assert target_runtime.read_text()==snapshot


def test_no_private_config_keeps_server_configuration_unchanged(tmp_path):
    app=tmp_path/'app'; app.mkdir()
    data=tmp_path/'data'; (data/'config').mkdir(parents=True)
    runtime=data/'config/runtime.json'; runtime.write_text('{"formal":"preserve"}')
    assert not import_providers(app,data)
    assert runtime.read_text()=='{"formal":"preserve"}'


def test_bundle_rejects_database_override_before_writing(tmp_path):
    app=tmp_path/'app'; (app/'.deployment-private').mkdir(parents=True)
    bundle=app/'.deployment-private/providers.json'
    bundle.write_text(json.dumps({'version':1,'runtime':{'AGICO_KB_DATABASE_URL':'overwrite'},
                                  'profiles':{'primary':'api=test\nmodel=glm-5.3-flash'}}))
    data=tmp_path/'data'; (data/'config').mkdir(parents=True)
    runtime=data/'config/runtime.json'; runtime.write_text('{"formal":"preserve"}')
    with pytest.raises(ValueError):
        import_providers(app,data)
    assert runtime.read_text()=='{"formal":"preserve"}'

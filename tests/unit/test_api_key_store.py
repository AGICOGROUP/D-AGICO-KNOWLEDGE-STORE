from dataclasses import replace
from pathlib import Path

import pytest

from agico_kb.config import Settings


def test_atomic_replacement_has_no_old_versions_and_is_read_by_existing_consumers(tmp_path):
    from agico_kb.api_keys import replace_key
    from agico_kb.vision import _credential_models
    settings = Settings('unused', tmp_path / 'originals', api_keys_dir=tmp_path / 'config/keys')
    replace_key(settings, 'fallback', 'fresh-first-secret')
    replace_key(settings, 'fallback', 'fresh-second-secret')
    path = settings.provider_profile('fallback')
    key, models = _credential_models(path)
    assert key == 'fresh-second-secret' and models == ('glm-5.3-flashx', 'glm-5.3-flash','deepseek-v4.1-flash-1017','deepseek-v4.1-flash')
    assert list(path.parent.iterdir()) == [path]
    assert 'fresh-first-secret' not in path.read_text()
    assert not path.is_relative_to(settings.storage_root)


def test_missing_legacy_profile_can_be_configured_without_restart(tmp_path):
    from agico_kb.api_keys import replace_key
    settings = Settings('unused', tmp_path / 'originals', api_keys_dir=tmp_path / 'config/keys')
    assert settings.provider_profile('primary') is None
    replace_key(settings, 'primary', 'fresh-primary-secret')
    assert settings.provider_profile('primary').is_file()


def test_key_directory_cannot_be_a_document_folder_or_source_directory(tmp_path):
    from agico_kb.api_keys import replace_key
    settings = Settings('unused', tmp_path / 'originals', api_keys_dir=tmp_path / 'originals/keys')
    with pytest.raises(ValueError):
        replace_key(settings, 'primary', 'fresh-primary-secret')


def test_operator_migration_removes_live_legacy_copy_and_preserves_models(tmp_path):
    from agico_kb.api_keys import migrate_legacy
    legacy = tmp_path / 'legacy.txt'
    legacy.write_text('api=fresh-legacy-secret\nmodel=glm-5.3-flashx\n')
    settings = Settings('unused', tmp_path / 'originals', vision_config_file=legacy,
                        api_keys_dir=tmp_path / 'config/keys')
    migrate_legacy(settings)
    assert not legacy.exists()
    from agico_kb.vision import _credential_models
    assert _credential_models(settings.provider_profile('primary')) == ('fresh-legacy-secret', ('glm-5.3-flashx','glm-5.3-flash'))
    migrate_legacy(settings)
def test_failed_replacement_keeps_only_the_previous_key(tmp_path, monkeypatch):
    from agico_kb.config import Settings
    from agico_kb.api_keys import replace_key
    import pytest
    settings = Settings('unused', tmp_path/'originals', api_keys_dir=tmp_path/'keys')
    replace_key(settings, 'primary', 'fresh-original-synthetic')
    def fail(*args):
        raise OSError('synthetic disk failure')
    monkeypatch.setattr('agico_kb.api_keys.os.replace', fail)
    with pytest.raises(OSError):
        replace_key(settings, 'primary', 'fresh-replacement-synthetic')
    assert [path.name for path in settings.api_keys_dir.iterdir()] == ['primary.txt']
    from agico_kb.vision import _credentials
    assert _credentials(settings.api_keys_dir/'primary.txt')[0] == 'fresh-original-synthetic'
    assert 'fresh-replacement-synthetic' not in (settings.api_keys_dir/'primary.txt').read_text()



def test_operator_refuses_to_remove_another_instances_credentials(tmp_path, monkeypatch, capsys):
    import json
    from agico_kb.api_keys import main
    other = tmp_path/'other-instance'
    other.mkdir()
    secret = other/'primary.txt'
    secret.write_text('api=fresh-other-instance-key\nmodel=glm-5.3-flash')
    current = tmp_path/'current-instance'
    current.mkdir()
    runtime = current/'runtime.json'
    data = {'AGICO_KB_STORAGE_ROOT':str(current/'originals'), 'AGICO_KB_VISION_CONFIG_FILE':str(secret)}
    runtime.write_text(json.dumps(data))
    monkeypatch.setattr('sys.argv', ['api_keys','--migrate','--runtime',str(runtime)])
    assert main() == 1
    assert secret.is_file() and json.loads(runtime.read_text()) == data
    assert 'fresh-other-instance-key' not in capsys.readouterr().err

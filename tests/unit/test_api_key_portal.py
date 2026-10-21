from pathlib import Path


def test_key_editor_is_write_only_and_reuses_the_account_session():
    html = Path('src/agico_kb/portal/index.html').read_text(encoding='utf-8')
    script = Path('src/agico_kb/portal/api-keys.js').read_text(encoding='utf-8')
    assert 'id="manage-api-keys"' in html and 'id="api-keys-dialog"' in html
    for name in ('primary','fallback'):
        assert f'id="api-key-{name}" type="password"' in html
    assert 'window.agicoAccount.request' in script
    assert 'superadmin' in script
    assert 'localStorage' not in script and 'sessionStorage' not in script
    assert 'type="text"' not in script and '.select()' not in script
    assert 'api_key_replaced' not in html


def test_supported_models_are_clickable_under_each_key():
    html=Path('src/agico_kb/portal/index.html').read_text(encoding='utf-8')
    script=Path('src/agico_kb/portal/api-keys.js').read_text(encoding='utf-8')
    for name in ('primary','fallback'):
        assert f'id="api-key-models-{name}"' in html
    assert 'input.type = "radio"' in script and '"/model"' in script
    assert '主线路' in html and '备用线路' in html and '公司主线路' not in html
    assert 'type="range" min="0" max="2" step="1"' in html
    assert 'masked_key' in script and 'api-key-change-' in script
    assert '30 分钟' in html and '加密保存' in html

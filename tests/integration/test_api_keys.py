from dataclasses import replace
from uuid import uuid4

from fastapi.testclient import TestClient

from agico_kb.accounts import bootstrap_superadmin


def actors(kb):
    client, db, root = kb
    db.settings = replace(db.settings, api_keys_dir=root.parent / (root.name + '-keys'))
    client.base_url = 'https://testserver'
    password = 'Fresh-' + uuid4().hex
    email = uuid4().hex + '@example.test'
    bootstrap_superadmin(db, email, password, must_change=False)
    auth = client.post('/v1/auth/login', json={'email': email, 'password': password}).json()
    headers = {'X-CSRF-Token': auth['csrf_token']}
    browsers = []
    for role in ('manager', 'user'):
        invited = client.post('/v1/accounts/invitations', headers=headers, json={
            'email': uuid4().hex + '@example.test', 'role': role, 'organizations': ['baiste']}).json()
        other = TestClient(client.app, base_url='https://testserver')
        identity = other.post('/v1/auth/activate', json={
            'invitation': invited['invitation'], 'password': password, 'password_confirm': password}).json()
        browsers.append((other, {'X-CSRF-Token': identity['csrf_token']}))
    return client, headers, db, browsers


def test_developers_can_replace_but_never_read_keys(kb):
    client, headers, db, others = actors(kb)
    initial = client.get('/v1/settings/api-keys')
    assert initial.status_code == 200
    assert not initial.json()['profiles']['primary']['configured']
    for profile in ('primary', 'fallback'):
        first, second = 'synthetic-' + uuid4().hex, 'replacement-' + uuid4().hex
        path = '/v1/settings/api-keys/' + profile
        saved = client.put(path, headers=headers, json={'api_key': first})
        assert saved.status_code == 200, saved.text
        assert first not in saved.text
        assert client.put(path, headers=headers, json={'api_key': second}).status_code == 200
        status = client.get('/v1/settings/api-keys')
        assert status.json()['profiles'][profile]['configured']
        assert status.json()['profiles'][profile]['updated_at']
        assert first not in status.text and second not in status.text
        files = [p for p in db.settings.api_keys_dir.iterdir() if p.is_file()]
        contents = '\n'.join(p.read_text() for p in files)
        assert first not in contents and second not in contents
        from agico_kb.vision import _credentials
        assert _credentials(db.settings.provider_profile(profile))[0] == second
    for browser, auth in others:
        assert browser.get('/v1/settings/api-keys').status_code == 403
        assert browser.put('/v1/settings/api-keys/primary', headers=auth,
                           json={'api_key': 'synthetic-' + uuid4().hex}).status_code == 403
    assert client.put('/v1/settings/api-keys/primary', json={'api_key': 'secret-not-logged'}).status_code == 403
    assert client.get('/v1/settings/api-keys/primary').status_code in (404, 405)
    with db.connection() as conn:
        events = conn.execute("SELECT detail FROM account_audit WHERE action='api_key_replaced'").fetchall()
        assert len(events) == 4
        assert all(set(event['detail']) == {'profile'} for event in events)


def test_invalid_keys_never_echo_or_overwrite_the_current_value(kb):
    client, headers, db, _ = actors(kb)
    path = '/v1/settings/api-keys/primary'
    secret = 'synthetic-' + uuid4().hex
    assert client.put(path, headers=headers, json={'api_key': secret}).status_code == 200
    for invalid in ('', '  ', 'private-value\nmodel=untrusted', 'private-value\r\nAuthorization: attacker'):
        result = client.put(path, headers=headers, json={'api_key': invalid})
        assert result.status_code == 422
        assert invalid not in result.text if invalid.strip() else True
        assert secret not in result.text
    assert client.put(path, headers={**headers, 'Origin': 'https://evil.invalid'},
                      json={'api_key': 'rejected-' + uuid4().hex}).status_code == 403
    from agico_kb.vision import _credentials
    assert _credentials(db.settings.provider_profile('primary'))[0] == secret


def test_probe_uses_only_new_input_and_sanitizes_provider_failures(kb, monkeypatch):
    import httpx
    client, headers, db, _ = actors(kb)
    secret = 'new-input-' + uuid4().hex
    def request(method, url, **kwargs):
        assert url == 'https://open.bigmodel.cn/api/paas/v4/chat/completions'
        assert kwargs['headers']['Authorization'] == 'Bearer ' + secret
        return httpx.Response(401, json={'error': secret})
    monkeypatch.setattr('agico_kb.api_keys.provider_request', request)
    result = client.post('/v1/settings/api-keys/primary/test', headers=headers, json={'api_key': secret})
    assert result.status_code == 200 and result.json()['ok'] is False
    assert secret not in result.text
    assert not client.get('/v1/settings/api-keys').json()['profiles']['primary']['configured']


def test_model_selection_is_developer_only_and_does_not_require_key_reentry(kb):
    from agico_kb.vision import _credentials
    client,headers,db,others=actors(kb)
    secret='fresh-key-'+uuid4().hex
    assert client.put('/v1/settings/api-keys/fallback',headers=headers,json={'api_key':secret}).status_code==200
    path='/v1/settings/api-keys/fallback/model'
    for browser,auth in others:
        assert browser.put(path,headers=auth,json={'model':'glm-5.3-flash'}).status_code==403
    assert client.put(path,json={'model':'glm-5.3-flash'}).status_code==403
    result=client.put(path,headers=headers,json={'model':'glm-5.3-flash'})
    assert result.status_code==200 and secret not in result.text
    assert result.json()['profiles']['fallback']['selected_model']=='glm-5.3-flash'
    assert _credentials(db.settings.provider_profile('fallback'))==(secret,'glm-5.3-flash')
    assert client.put(path,headers=headers,json={'model':'deepseek-v4-flash'}).status_code==422


def test_mask_and_three_level_settings_are_developer_only(kb):
    client,headers,db,others=actors(kb)
    key='abc-'+uuid4().hex+'7890'
    assert client.put('/v1/settings/api-keys/fallback',headers=headers,json={'api_key':key}).status_code==200
    path='/v1/settings/api-keys/fallback/model'
    payload={'model':'deepseek-v4.1-flash-1017','reasoning_effort':'high'}
    for browser,auth in others:
        assert browser.put(path,headers=auth,json=payload).status_code==403
        assert browser.get('/v1/settings/api-keys').status_code==403
    result=client.put(path,headers=headers,json=payload)
    assert result.status_code==200 and key not in result.text
    data=result.json()['profiles']['fallback']
    assert data['masked_key']=='abc************7890'
    assert data['reasoning_effort']=='high'
    assert client.put(path,headers=headers,json={**payload,'reasoning_effort':'ultra'}).status_code==422


def test_stored_key_connection_test_never_returns_the_key(kb,monkeypatch):
    import httpx
    client,headers,db,others=actors(kb)
    secret='fresh-test-'+uuid4().hex
    assert client.put('/v1/settings/api-keys/primary',headers=headers,json={'api_key':secret}).status_code==200
    def request(method,url,**kwargs):
        assert kwargs['headers']['Authorization']=='Bearer '+secret
        assert kwargs['json']['reasoning_effort']=='low'
        return httpx.Response(200,json={'choices':[{'message':{'content':'OK'}}]})
    monkeypatch.setattr('agico_kb.api_keys.provider_request',request)
    result=client.post('/v1/settings/api-keys/primary/test',headers=headers)
    assert result.status_code==200 and result.json()['ok']
    assert secret not in result.text
    for browser,auth in others:
        assert browser.post('/v1/settings/api-keys/primary/test',headers=auth).status_code==403

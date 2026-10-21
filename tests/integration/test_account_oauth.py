import asyncio
import base64
import hashlib
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlencode, urlsplit
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from mcp.server.auth.provider import TokenError
from mcp.shared.auth import OAuthClientInformationFull

from agico_kb.account_oauth import Provider, _context
from agico_kb.accounts import bootstrap_superadmin
from agico_kb.auth import token_hash


def test_mcp_account_authorization_pkce_replay_refresh_and_revocation(kb):
    outer, db, _ = kb
    browser = TestClient(outer.app, base_url='http://localhost:18791')
    email, password = uuid4().hex+'@example.test', 'Fresh-'+uuid4().hex
    principal = bootstrap_superadmin(db,email,password,must_change=False)
    me = browser.post('/v1/auth/login',json={'email':email,'password':password}).json()
    resource = 'http://localhost:18791/mcp'
    assert browser.post('/mcp',json={}).status_code == 401
    metadata = browser.get('/.well-known/oauth-protected-resource').json()
    assert metadata['resource'] == resource
    server = browser.get('/.well-known/oauth-authorization-server').json()
    assert 'S256' in server['code_challenge_methods_supported']
    redirect = 'http://127.0.0.1:19876/callback'
    registered = browser.post('/register',json={'client_name':'Fresh MCP client',
        'redirect_uris':[redirect],'token_endpoint_auth_method':'none',
        'grant_types':['authorization_code','refresh_token'],'response_types':['code']})
    assert registered.status_code == 201, registered.text
    client_id = registered.json()['client_id']
    verifier = uuid4().hex + uuid4().hex
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
    authorization = browser.get('/authorize?'+urlencode({'client_id':client_id,'redirect_uri':redirect,
        'response_type':'code','code_challenge':challenge,'code_challenge_method':'S256',
        'scope':'kb:access','resource':resource,'state':'fresh-state'}),follow_redirects=False)
    assert authorization.status_code in {302,303}, authorization.text
    request_id = parse_qs(urlsplit(authorization.headers['location']).query)['oauth'][0]
    consent = browser.post('/v1/auth/oauth-consent',json={'request':request_id,'approved':True},
                           headers={'X-CSRF-Token':me['csrf_token']})
    assert consent.status_code == 200, consent.text
    callback = parse_qs(urlsplit(consent.json()['redirect']).query)
    assert callback['state'] == ['fresh-state']
    code = callback['code'][0]
    exchange = {'grant_type':'authorization_code','client_id':client_id,'code':code,
                'code_verifier':verifier,'redirect_uri':redirect,'resource':resource}
    assert browser.post('/token',data={**exchange,'code_verifier':'wrong'}).status_code == 400
    assert browser.post('/token',data={**exchange,'resource':'https://wrong.invalid/mcp'}).status_code == 400
    issued = browser.post('/token',data=exchange)
    assert issued.status_code == 200, issued.text
    access = issued.json()['access_token']
    refresh = issued.json()['refresh_token']
    assert browser.post('/token',data=exchange).status_code == 400
    browser.cookies.clear()
    assert browser.get('/v1/catalog',headers={'Authorization':'Bearer '+access}).status_code == 200
    refreshed = browser.post('/token',data={'grant_type':'refresh_token','client_id':client_id,
        'refresh_token':refresh,'resource':resource})
    assert refreshed.status_code == 200, refreshed.text
    rotated = refreshed.json()['access_token']
    assert rotated != access
    assert browser.get('/v1/catalog',headers={'Authorization':'Bearer '+access}).status_code == 401
    with db.connection(write=True) as conn:
        conn.execute("UPDATE account_users SET status='disabled' WHERE principal_id=%s",(principal,))
    assert browser.get('/v1/catalog',headers={'Authorization':'Bearer '+rotated}).status_code == 401


def test_refresh_replay_after_two_loads_revokes_successor_family(kb):
    _, db, _ = kb
    principal = bootstrap_superadmin(db,uuid4().hex+'@example.test','Fresh-'+uuid4().hex,must_change=False)
    provider = Provider(db)
    identity = uuid4().hex
    resource = 'http://localhost:18791/mcp'
    client = OAuthClientInformationFull(client_id=identity,client_name='Fresh replay test',
        redirect_uris=['http://127.0.0.1:19876/callback'],token_endpoint_auth_method='none')

    async def race():
        await provider.register_client(client)
        with db.connection(write=True) as conn:
            grant = uuid4()
            conn.execute('INSERT INTO oauth_grants(id,principal_id,client_id,resource) VALUES (%s,%s,%s,%s)',
                (grant,principal,identity,resource))
            credentials = provider.mint(conn,grant)
        # Both requests inspect the credential while it is still unused.
        first = await provider.load_refresh_token(client,credentials.refresh_token)
        second = await provider.load_refresh_token(client,credentials.refresh_token)
        assert first and second
        marker = _context.set({'resource':resource})
        try:
            successor = await provider.exchange_refresh_token(client,first,['kb:access'])
            with pytest.raises(TokenError):
                await provider.exchange_refresh_token(client,second,['kb:access'])
            assert await provider.load_access_token(successor.access_token) is None
            assert await provider.load_refresh_token(client,successor.refresh_token) is None
        finally:
            _context.reset(marker)
    asyncio.run(race())


@pytest.mark.parametrize('mutation',['password','permissions'])
def test_unused_authorization_code_is_revoked_with_account_changes(kb, mutation):
    browser, db, _ = kb
    email, password = uuid4().hex+'@example.test', 'Fresh-'+uuid4().hex
    principal = bootstrap_superadmin(db,email,password,must_change=False)
    me = browser.post('/v1/auth/login',json={'email':email,'password':password}).json()
    provider = Provider(db)
    identity, code = uuid4().hex, uuid4().hex+uuid4().hex
    resource, redirect = 'http://testserver/mcp','http://127.0.0.1:19876/callback'
    client = OAuthClientInformationFull(client_id=identity,client_name='Fresh revoke test',
        redirect_uris=[redirect],token_endpoint_auth_method='none')

    async def scenario():
        await provider.register_client(client)
        with db.connection(write=True) as conn:
            conn.execute('INSERT INTO oauth_codes(code_hash,principal_id,client_id,redirect_uri,resource,challenge,expires_at) VALUES (%s,%s,%s,%s,%s,%s,%s)',
                (token_hash(code),principal,identity,redirect,resource,'new-challenge',datetime.now(UTC)+timedelta(minutes=5)))
        cached = await provider.load_authorization_code(client,code)
        assert cached
        if mutation == 'password':
            changed = browser.post('/v1/auth/change-password',headers={'X-CSRF-Token':me['csrf_token']},json={
                'current_password':password,'password':'Changed-'+password,'password_confirm':'Changed-'+password})
        else:
            changed = browser.patch('/v1/accounts/'+principal,headers={'X-CSRF-Token':me['csrf_token']},json={
                'role':'superadmin','organizations':[],'active':True})
        assert changed.status_code == 200
        marker = _context.set({'resource':resource})
        try:
            with pytest.raises(TokenError):
                await provider.exchange_authorization_code(client,cached)
            assert await provider.load_authorization_code(client,code) is None
        finally:
            _context.reset(marker)
    asyncio.run(scenario())


def test_inflight_consent_rechecks_session_after_password_change(kb, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from mcp.server.auth.provider import AuthorizationParams

    from agico_kb import accounts

    owner, db, _ = kb
    email, password = uuid4().hex+'@example.test', 'Fresh-'+uuid4().hex
    bootstrap_superadmin(db,email,password,must_change=False)
    me = owner.post('/v1/auth/login',json={'email':email,'password':password}).json()
    client_id, pending = uuid4().hex, uuid4().hex+uuid4().hex
    redirect, resource = 'http://127.0.0.1:19876/callback','http://testserver/mcp'
    info = OAuthClientInformationFull(client_id=client_id,client_name='Fresh concurrent consent',
        redirect_uris=[redirect],token_endpoint_auth_method='none')
    asyncio.run(Provider(db).register_client(info))
    params = AuthorizationParams(state='fresh',scopes=['kb:access'],code_challenge='fresh-challenge',
        redirect_uri=redirect,redirect_uri_provided_explicitly=True,resource=resource)
    with db.connection(write=True) as conn:
        conn.execute('INSERT INTO oauth_pending(request_hash,client_id,parameters) VALUES (%s,%s,%s::jsonb)',
            (token_hash(pending),client_id,params.model_dump_json()))
    browser = TestClient(owner.app)
    browser.cookies.update(owner.cookies)
    arrived, resume = Event(), Event()
    original = accounts.require_current_session

    def delayed(conn, request):
        arrived.set()
        assert resume.wait(5)
        return original(conn,request)

    monkeypatch.setattr(accounts,'require_current_session',delayed)
    with ThreadPoolExecutor(max_workers=1) as pool:
        consent = pool.submit(browser.post,'/v1/auth/oauth-consent',json={'request':pending,'approved':True},
            headers={'X-CSRF-Token':me['csrf_token']})
        try:
            assert arrived.wait(5)
            changed = owner.post('/v1/auth/change-password',headers={'X-CSRF-Token':me['csrf_token']},json={
                'current_password':password,'password':'Changed-'+password,'password_confirm':'Changed-'+password})
            assert changed.status_code == 200
        finally:
            resume.set()
        assert consent.result(timeout=5).status_code == 401
    with db.connection() as conn:
        assert conn.execute('SELECT count(*) AS n FROM oauth_codes').fetchone()['n'] == 0

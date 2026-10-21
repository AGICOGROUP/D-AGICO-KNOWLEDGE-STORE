from dataclasses import replace
import asyncio
from uuid import uuid4

import pytest

from agico_kb.accounts import bootstrap_superadmin
from agico_kb.dingtalk_client import DingTalkConfig, Employee
from agico_kb.dingtalk_identity import IdentityService
from agico_kb.errors import KBError


class FakeDingTalk:
    def __init__(self):
        self.config = DingTalkConfig('ding-test', 'key', 'synthetic-only', '123', {'10': ['baiste'], '20': ['xingyuan']})
        self.current = Employee('u1', '合成测试员工', ('10',), True)
        self.fail = False
        self.denied = False
        self.visible_allowed = True
        self.calls = 0

    def identity(self, code):
        if code != 'fresh-code':
            raise KBError('DINGTALK_REJECTED', 'invalid synthetic code', 403)
        return self.employee(self.current.user_id)

    def employee(self, user_id):
        self.calls += 1
        if self.fail:
            raise KBError('DINGTALK_UNAVAILABLE', 'synthetic timeout', 503)
        if self.denied:
            raise KBError('DINGTALK_IDENTITY_REMOVED', 'synthetic removal', 403)
        return self.current

    def departments(self):
        return []

    def organizations(self, ids, hierarchy):
        return {org for dep in ids for org in self.config.department_map.get(dep, [])}

    def visible(self, employee, hierarchy):
        return self.visible_allowed


@pytest.fixture
def ding(kb):
    client, db, root = kb
    admin = bootstrap_superadmin(db, 'fresh-admin@example.test', 'fresh-password', must_change=False)
    fake = FakeDingTalk()
    fake.config = replace(fake.config, maintenance_principals=(admin,))
    db.dingtalk = IdentityService(db, fake)
    return client, db, fake, admin


def signin(client):
    challenge = client.post('/v1/auth/dingtalk/challenge').json()['challenge']
    return client.post('/v1/auth/dingtalk/login', json={'code': 'fresh-code', 'challenge': challenge})


def expire(db):
    with db.connection(write=True) as conn:
        conn.execute("UPDATE dingtalk_identities SET verified_at=now()-interval '10 minutes'")


def test_passwordless_staff_session_has_scoped_memberships(ding):
    client, db, fake, _ = ding
    result = signin(client)
    assert result.status_code == 200, result.text
    assert result.json()['auth_provider'] == 'dingtalk'
    assert result.json()['role'] == 'user'
    assert result.json()['display_name'] == fake.current.name
    assert client.get('/v1/auth/me').status_code == 200
    with db.connection() as conn:
        user = conn.execute("SELECT * FROM account_users WHERE email LIKE '%@dingtalk.invalid'").fetchone()
        assert user['password_hash'] is None
        assert user['account_role'] == 'user'
    catalog = client.get('/v1/catalog').json()
    assert 'xingyuan' not in str(catalog)


def test_challenge_is_one_use_and_same_browser(ding):
    client, db, fake, _ = ding
    challenge = client.post('/v1/auth/dingtalk/challenge').json()['challenge']
    cookie = client.cookies.get('agico_dingtalk_challenge')
    client.cookies.clear()
    assert client.post('/v1/auth/dingtalk/login', json={'code': 'fresh-code', 'challenge': challenge}).status_code == 403
    client.cookies.set('agico_dingtalk_challenge', cookie)
    assert client.post('/v1/auth/dingtalk/login', json={'code': 'fresh-code', 'challenge': challenge}).status_code == 200
    assert client.post('/v1/auth/dingtalk/login', json={'code': 'fresh-code', 'challenge': challenge}).status_code == 403


def test_old_password_entry_blocked_except_explicit_maintenance(ding):
    client, db, fake, admin = ding
    assert client.post('/v1/auth/login', json={'email': 'fresh-admin@example.test', 'password': 'fresh-password'}).status_code == 200
    csrf = client.get('/v1/auth/me').json()['csrf_token']
    result = client.post('/v1/accounts/invitations', headers={'X-CSRF-Token': csrf}, json={
        'email': 'staff@example.test', 'role': 'user', 'organizations': ['baiste']})
    assert result.status_code == 409
    assert client.post('/v1/auth/activate', json={'invitation': 'x'*32, 'password': 'new-password', 'password_confirm': 'new-password'}).status_code == 409


def test_department_move_updates_scope_without_login(ding):
    client, db, fake, _ = ding
    assert signin(client).status_code == 200
    fake.current = replace(fake.current, department_ids=('20',))
    expire(db)
    assert client.get('/v1/auth/me').status_code == 200
    from agico_kb.accounts import authenticate_session
    principal, _ = authenticate_session(db, client.cookies.get('agico_session'))
    assert 'baiste' not in principal.memberships
    assert 'xingyuan' in principal.memberships


def test_identity_freshness_cannot_outlive_cached_department_evidence(ding):
    from datetime import datetime, timedelta, timezone
    client, db, fake, _ = ding
    assert signin(client).status_code == 200
    evidence = datetime.now(timezone.utc) - timedelta(seconds=290)
    db.dingtalk._hierarchy_at = evidence
    assert signin(client).status_code == 200
    with db.connection() as conn:
        assert conn.execute('SELECT verified_at FROM dingtalk_identities').fetchone()['verified_at'] <= evidence


def test_slow_refresh_cannot_overwrite_a_newer_identity_snapshot(ding):
    client, db, fake, _ = ding
    assert signin(client).status_code == 200
    with db.connection() as conn:
        owner = conn.execute('SELECT principal_id FROM dingtalk_identities').fetchone()['principal_id']
    expire(db)
    def overlapping_refresh(user_id):
        old = fake.current
        fake.current = replace(old, department_ids=('20',))
        # Another API process logs in while this process's older request is in flight.
        IdentityService(db, fake).login(fake.current)
        return old
    fake.employee = overlapping_refresh
    assert db.dingtalk.ensure_fresh(owner)
    with db.connection() as conn:
        assert conn.execute('SELECT organization_ids FROM dingtalk_identities').fetchone()['organization_ids'] == ['xingyuan']


def test_disabled_employee_loses_sessions_and_grants(ding):
    client, db, fake, _ = ding
    assert signin(client).status_code == 200
    fake.current = replace(fake.current, active=False)
    expire(db)
    assert client.get('/v1/auth/me').status_code == 401
    with db.connection() as conn:
        assert conn.execute('SELECT count(*) AS n FROM account_sessions').fetchone()['n'] == 0


def test_provider_error_does_not_extend_freshness_or_disable_everyone(ding):
    client, db, fake, _ = ding
    assert signin(client).status_code == 200
    expire(db)
    fake.fail = True
    assert client.get('/v1/auth/me').status_code in (401, 503)
    with db.connection() as conn:
        row = conn.execute('SELECT * FROM dingtalk_identities').fetchone()
        assert row['enabled']
    fake.fail = False
    assert client.get('/v1/auth/me').status_code == 200


def test_cross_origin_and_client_fields_rejected(ding):
    client, _, fake, _ = ding
    assert client.post('/v1/auth/dingtalk/challenge', headers={'Origin': 'https://evil.invalid'}).status_code == 403
    challenge = client.post('/v1/auth/dingtalk/challenge').json()['challenge']
    assert client.post('/v1/auth/dingtalk/login', json={'code': 'fresh-code', 'challenge': challenge, 'role': 'superadmin'}).status_code == 422


def test_existing_account_requires_verified_binding_not_same_name(ding):
    client, db, fake, admin = ding
    fake.current = replace(fake.current, name='fresh-admin@example.test')
    assert signin(client).json()['role'] == 'user'
    with db.connection() as conn:
        assert conn.execute('SELECT principal_id FROM dingtalk_identities').fetchone()['principal_id'] != admin


def test_verified_binding_preserves_existing_principal_and_hash(ding):
    client, db, fake, admin = ding
    assert client.post('/v1/auth/login', json={'email': 'fresh-admin@example.test', 'password': 'fresh-password'}).status_code == 200
    csrf = client.get('/v1/auth/me').json()['csrf_token']
    with db.connection() as conn:
        old = conn.execute('SELECT password_hash FROM account_users WHERE principal_id=%s', (admin,)).fetchone()['password_hash']
    challenge = client.post('/v1/auth/dingtalk/challenge').json()['challenge']
    result = client.post('/v1/auth/dingtalk/bind', headers={'X-CSRF-Token': csrf}, json={'code': 'fresh-code', 'challenge': challenge})
    assert result.status_code == 200, result.text
    with db.connection() as conn:
        assert conn.execute('SELECT principal_id FROM dingtalk_identities').fetchone()['principal_id'] == admin
        assert conn.execute('SELECT password_hash FROM account_users WHERE principal_id=%s', (admin,)).fetchone()['password_hash'] == old


def test_binding_rechecks_session_after_remote_identity_exchange(ding):
    from agico_kb import accounts
    client, db, fake, admin = ding
    local = client.post('/v1/auth/login', json={'email': 'fresh-admin@example.test', 'password': 'fresh-password'}).json()
    challenge = client.post('/v1/auth/dingtalk/challenge').json()['challenge']
    original = fake.identity
    def identity_and_revoke(code):
        employee = original(code)
        with db.connection(write=True) as conn:
            accounts.revoke_sessions(conn, admin)
        return employee
    fake.identity = identity_and_revoke
    result = client.post('/v1/auth/dingtalk/bind', headers={'X-CSRF-Token': local['csrf_token']},
                         json={'code': 'fresh-code', 'challenge': challenge})
    assert result.status_code == 401, result.text
    with db.connection() as conn:
        assert conn.execute('SELECT count(*) AS n FROM dingtalk_identities').fetchone()['n'] == 0


def test_mcp_credentials_revalidate_without_a_browser_login(ding):
    from agico_kb.account_oauth import Provider
    from mcp.shared.auth import OAuthClientInformationFull
    client, db, fake, _ = ding
    assert signin(client).status_code == 200
    provider = Provider(db)
    async def scenario():
        oauth_client = OAuthClientInformationFull(client_id=uuid4().hex, client_name='Fresh DingTalk test',
            redirect_uris=['http://127.0.0.1:19761/callback'], token_endpoint_auth_method='none')
        await provider.register_client(oauth_client)
        with db.connection(write=True) as conn:
            owner = conn.execute('SELECT principal_id FROM dingtalk_identities').fetchone()['principal_id']
            grant = uuid4()
            conn.execute('INSERT INTO oauth_grants(id,principal_id,client_id,resource) VALUES (%s,%s,%s,%s)',
                         (grant, owner, oauth_client.client_id, 'http://testserver/mcp'))
            tokens = provider.mint(conn, grant)
        expire(db)
        # A still-active employee renews its directory evidence through MCP alone.
        assert await provider.load_access_token(tokens.access_token) is not None
        fake.current = replace(fake.current, active=False)
        expire(db)
        assert await provider.load_access_token(tokens.access_token) is None
        assert await provider.load_refresh_token(oauth_client, tokens.refresh_token) is None
        with db.connection() as conn:
            assert conn.execute('SELECT revoked FROM oauth_grants WHERE id=%s', (grant,)).fetchone()['revoked']
    asyncio.run(scenario())


def test_removed_employee_old_cookie_stays_revoked_after_recovery(ding):
    client, db, fake, _ = ding
    assert signin(client).status_code == 200
    expire(db)
    fake.denied = True
    assert client.get('/v1/auth/me').status_code == 401
    fake.denied = False
    expire(db)
    assert client.get('/v1/auth/me').status_code == 401
    assert signin(client).status_code == 200


def test_bound_developer_keeps_independent_local_maintenance_login(ding):
    client, db, fake, admin = ding
    local = client.post('/v1/auth/login', json={'email': 'fresh-admin@example.test', 'password': 'fresh-password'}).json()
    challenge = client.post('/v1/auth/dingtalk/challenge').json()['challenge']
    assert client.post('/v1/auth/dingtalk/bind', headers={'X-CSRF-Token': local['csrf_token']},
        json={'code': 'fresh-code', 'challenge': challenge}).status_code == 200
    expire(db)
    fake.fail = True
    assert client.post('/v1/auth/login', json={'email': 'fresh-admin@example.test', 'password': 'fresh-password'}).status_code == 200
    assert client.get('/v1/auth/me').status_code == 200
    assert client.get('/v1/auth/me').json()['auth_provider'] == 'local'


def test_application_scope_removal_revokes_cookie_even_if_employee_active(ding):
    client, db, fake, _ = ding
    assert signin(client).status_code == 200
    fake.visible_allowed = False
    expire(db)
    assert client.get('/v1/auth/me').status_code == 401
    fake.visible_allowed = True
    expire(db)
    assert client.get('/v1/auth/me').status_code == 401


def test_new_dingtalk_instance_does_not_require_legacy_password_initialization(ding):
    client, db, fake, _ = ding
    with db.connection(write=True) as conn:
        conn.execute('UPDATE account_settings SET enabled=false')
    assert client.get('/v1/auth/settings').json()['enabled'] is True
    assert signin(client).status_code == 200
    assert client.get('/v1/catalog').status_code == 200
    from agico_kb.auth import authenticate
    from agico_kb.account_oauth import Provider
    from mcp.shared.auth import OAuthClientInformationFull
    provider = Provider(db)
    async def scenario():
        app = OAuthClientInformationFull(client_id=uuid4().hex, client_name='New DingTalk instance test',
            redirect_uris=['http://127.0.0.1:19761/callback'], token_endpoint_auth_method='none')
        await provider.register_client(app)
        with db.connection(write=True) as conn:
            owner = conn.execute('SELECT principal_id FROM dingtalk_identities').fetchone()['principal_id']
            grant = uuid4()
            conn.execute('INSERT INTO oauth_grants(id,principal_id,client_id,resource) VALUES (%s,%s,%s,%s)',
                         (grant, owner, app.client_id, 'http://testserver/mcp'))
            tokens = provider.mint(conn, grant)
        assert authenticate(db, tokens.access_token, 'http://testserver/mcp') is not None
        assert authenticate(db, 'alice', 'http://testserver/mcp') is None
    asyncio.run(scenario())

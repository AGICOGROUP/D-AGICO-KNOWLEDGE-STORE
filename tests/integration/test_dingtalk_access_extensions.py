"""Fresh identities and schemas: mapped membership plus explicit read grants."""
from dataclasses import replace
import asyncio
from uuid import uuid4

from fastapi.testclient import TestClient

from agico_kb.accounts import authenticate_session
from test_dingtalk_identity import ding, expire, signin  # noqa: F401


def people(ding):
    client, db, fake, root_id = ding
    staff = TestClient(client.app)
    assert signin(staff).status_code == 200
    with db.connection() as conn:
        target = conn.execute('SELECT principal_id FROM dingtalk_identities').fetchone()['principal_id']
    fake.current = replace(fake.current, user_id='fresh-manager', name='合成管理员')
    manager = TestClient(client.app)
    assert signin(manager).status_code == 200
    with db.connection(write=True) as conn:
        mid = conn.execute("SELECT principal_id FROM dingtalk_identities WHERE user_id='fresh-manager'").fetchone()['principal_id']
        conn.execute("UPDATE account_users SET account_role='manager' WHERE principal_id=%s", (mid,))
        conn.execute("INSERT INTO memberships VALUES (%s,'baiste','publisher')", (mid,))
    root = client.post('/v1/auth/login', json={'email':'fresh-admin@example.test','password':'fresh-password'})
    assert root.status_code == 200
    return client, db, fake, root_id, staff, target, manager


def csrf(browser):
    return {'X-CSRF-Token': browser.get('/v1/auth/me').json()['csrf_token']}


def principal(browser, db):
    return authenticate_session(db, browser.cookies.get('agico_session'))[0]


def test_developer_extension_survives_refresh_and_removal_is_immediate(ding):
    root, db, fake, _, staff, target, _ = people(ding)
    path = f'/v1/accounts/{target}/access'
    assert 'xingyuan' not in principal(staff, db).memberships
    changed = root.patch(path, headers=csrf(root), json={'extra_organizations':['xingyuan']})
    assert changed.status_code == 200, changed.text
    assert set(changed.json()['extra_organizations']) == {'xingyuan'}
    current = principal(staff, db)
    assert {'baiste','xingyuan'} <= current.memberships.keys()
    assert not current.publisher('xingyuan')
    fake.current = replace(fake.current, user_id='u1', department_ids=('20',))
    expire(db)
    current = principal(staff, db)
    assert 'baiste' not in current.memberships
    assert 'xingyuan' in current.memberships
    # Extension remains independent even while it overlaps the new mapped base.
    listing = root.get('/v1/accounts').json()['items']
    row = next(row for row in listing if row['principal_id'] == target)
    assert row['extra_organizations'] == ['xingyuan']
    assert row['synchronized_organizations'] == ['xingyuan']
    fake.current = replace(fake.current, department_ids=('10',))
    expire(db)
    assert 'xingyuan' in principal(staff, db).memberships
    assert root.patch(path, headers=csrf(root), json={'extra_organizations':[]}).status_code == 200
    assert 'xingyuan' not in principal(staff, db).memberships
    assert principal(staff, db).memberships['baiste'] == 'member'


def test_manager_can_grant_other_divisions_without_role_or_publisher_escalation(ding):
    root, db, _, root_id, staff, target, manager = people(ding)
    listing = manager.get('/v1/accounts?tree=true').json()
    def flatten(rows):
        return [row for item in rows for row in [item, *flatten(item['children'])]]
    rows = flatten(listing['items'])
    row = next(row for row in rows if row['principal_id'] == target)
    assert row['can_edit_access']
    assert not row['can_manage']  # unrelated employee; role/status editing stays protected
    assert 'xingyuan' in {org['id'] for org in listing['access_organizations']}
    path = f'/v1/accounts/{target}/access'
    assert manager.patch(path, headers=csrf(manager), json={'extra_organizations':['xingyuan']}).status_code == 200
    assert principal(staff, db).memberships['xingyuan'] == 'member'
    assert manager.patch(path, headers=csrf(manager), json={'extra_organizations':['xingyuan'],'role':'superadmin'}).status_code == 422
    assert manager.patch(f'/v1/accounts/{root_id}/access', headers=csrf(manager), json={'extra_organizations':['xingyuan']}).status_code == 403
    assert manager.patch(f'/v1/accounts/{target}', headers=csrf(manager), json={'role':'manager','organizations':['baiste']}).status_code == 403
    mid = principal(manager, db).id
    assert root.patch(f'/v1/accounts/{mid}/access', headers=csrf(root), json={'extra_organizations':['xingyuan']}).status_code == 200
    assert principal(manager, db).publisher('baiste')
    assert not principal(manager, db).publisher('xingyuan')


def test_extensions_cannot_bypass_validation_csrf_or_inactive_identity(ding):
    root, db, fake, _, staff, target, _ = people(ding)
    path = f'/v1/accounts/{target}/access'
    assert staff.patch(path, headers=csrf(staff), json={'extra_organizations':['xingyuan']}).status_code == 403
    assert root.patch(path, json={'extra_organizations':['xingyuan']}).status_code == 403
    assert root.patch(path, headers=csrf(root), json={'extra_organizations':['unknown']}).status_code == 422
    assert root.patch(path, headers=csrf(root), json={'extra_organizations':['xingyuan','xingyuan']}).status_code == 200
    fake.current = replace(fake.current, user_id='u1', active=False)
    expire(db)
    assert staff.get('/v1/auth/me').status_code == 401


def test_published_file_and_existing_ai_token_follow_extensions(ding):
    from agico_kb.account_oauth import Provider
    from agico_kb.auth import authenticate
    from mcp.shared.auth import OAuthClientInformationFull
    root,db,_,_,staff,target,_=people(ding)
    rh=csrf(root)
    original=('fresh-access-'+uuid4().hex).encode()
    upload=root.post('/v1/uploads',headers={**rh,'Idempotency-Key':uuid4().hex},json={'filename':'fresh-extension.md','size':len(original)}).json()['upload_id']
    assert root.put(f'/v1/uploads/{upload}/content',headers=rh,content=original).status_code==200
    submitted=root.post('/v1/submissions',headers=rh,json={'upload_id':upload,'organization_id':'xingyuan','category_id':'technical','title':'合成跨事业部资料','visibility':'department','base_revision':0})
    assert submitted.status_code==201,submitted.text
    version=submitted.json()['version_id']
    with db.connection(write=True) as conn:
        conn.execute("UPDATE versions SET processing_status='ready' WHERE id=%s",(version,))
        conn.execute("UPDATE jobs SET state='complete' WHERE version_id=%s",(version,))
    assert root.post(f'/v1/versions/{version}/publish',headers=rh,json={'expected_revision':0}).status_code==200
    route=f'/v1/versions/{version}/content'
    assert staff.get(route).status_code==404
    provider=Provider(db)
    async def token():
        client=OAuthClientInformationFull(client_id=uuid4().hex,client_name='Fresh extension token',redirect_uris=['http://127.0.0.1:19761/callback'],token_endpoint_auth_method='none')
        await provider.register_client(client)
        with db.connection(write=True) as conn:
            grant=uuid4()
            conn.execute('INSERT INTO oauth_grants(id,principal_id,client_id,resource) VALUES (%s,%s,%s,%s)',(grant,target,client.client_id,'http://testserver/mcp'))
            return provider.mint(conn,grant).access_token
    secret=asyncio.run(token())
    def ai():return authenticate(db,secret,'http://testserver/mcp')
    assert 'xingyuan' not in ai().memberships
    assert root.patch(f'/v1/accounts/{target}/access',headers=rh,json={'extra_organizations':['xingyuan']}).status_code==200
    assert staff.get(route).content==original
    assert len(staff.get('/v1/documents').json()['items'])==1
    assert ai().memberships['xingyuan']=='member'
    assert root.patch(f'/v1/accounts/{target}/access',headers=rh,json={'extra_organizations':[]}).status_code==200
    assert staff.get(route).status_code==404
    assert not staff.get('/v1/documents').json()['items']
    assert 'xingyuan' not in ai().memberships

from uuid import uuid4

from agico_kb.accounts import bootstrap_superadmin


def login(client, email, password):
    response = client.post('/v1/auth/login', json={'email':email, 'password':password})
    assert response.status_code == 200, response.text
    return response.json()


def create_invite(client, csrf, email, role='user', orgs=None):
    response = client.post('/v1/accounts/invitations', headers={'X-CSRF-Token':csrf}, json={
        'email':email, 'role':role, 'organizations':orgs or ['baiste'],
    })
    assert response.status_code == 201, response.text
    return response.json()['invitation']


def activate(client, invitation, password):
    response = client.post('/v1/auth/activate', json={
        'invitation':invitation, 'password':password, 'password_confirm':password,
    })
    assert response.status_code == 200, response.text
    return response.json()


def test_superadmin_first_login_password_change_and_legacy_credential_rejection(kb):
    client, db, _ = kb
    email, password = 'root-' + uuid4().hex + '@example.test', 'Fresh-' + uuid4().hex
    bootstrap_superadmin(db, email, password)
    assert client.get('/v1/catalog', headers={'Authorization':'Bearer chief'}).status_code == 401
    me = login(client, email.upper(), password)
    assert me['role'] == 'superadmin' and me['must_change_password']
    cookie = client.cookies.get('agico_session')
    assert cookie and cookie not in str(me)
    assert client.get('/v1/catalog').status_code == 403
    assert client.post('/v1/auth/change-password', json={
        'current_password':password, 'password':'Changed-' + uuid4().hex,
        'password_confirm':'different',
    }, headers={'X-CSRF-Token':me['csrf_token']}).status_code == 422
    new_password = 'Changed-' + uuid4().hex
    changed = client.post('/v1/auth/change-password', json={
        'current_password':password, 'password':new_password, 'password_confirm':new_password,
    }, headers={'X-CSRF-Token':me['csrf_token']})
    assert changed.status_code == 200
    assert client.get('/v1/catalog').status_code == 200
    assert client.cookies.get('agico_session') != cookie
    client.cookies.clear()
    assert client.post('/v1/auth/login', json={'email':email, 'password':password}).status_code == 401
    me = login(client, email, new_password)
    assert not me['must_change_password']
    assert client.post('/v1/auth/logout', headers={'X-CSRF-Token':me['csrf_token']}).status_code == 200
    assert client.get('/v1/catalog').status_code == 401


def test_invited_manager_can_invite_own_department_accounts_and_links_are_single_use(kb):
    client, db, _ = kb
    root, password = 'root-' + uuid4().hex + '@example.test', 'Fresh-' + uuid4().hex
    bootstrap_superadmin(db, root, password, must_change=False)
    me = login(client, root, password)
    manager = 'manager-' + uuid4().hex + '@example.test'
    invitation = create_invite(client, me['csrf_token'], manager, 'manager')
    assert client.post('/v1/auth/invitation', json={'invitation':invitation}).json()['email'] == manager
    assert client.post('/v1/auth/activate', json={
        'invitation':invitation, 'password':password, 'password_confirm':'mismatch',
    }).status_code == 422
    assert activate(client, invitation, password)['role'] == 'manager'
    assert client.post('/v1/auth/activate', json={
        'invitation':invitation, 'password':password, 'password_confirm':password,
    }).status_code == 410
    manager_session = login(client, manager, password)
    staff = 'staff-' + uuid4().hex + '@example.test'
    staff_invite = create_invite(client, manager_session['csrf_token'], staff)
    for role, organizations in [('superadmin',['baiste']),('manager',['xingyuan']),('user',['xingyuan'])]:
        assert client.post('/v1/accounts/invitations', headers={'X-CSRF-Token':manager_session['csrf_token']},
                           json={'email':uuid4().hex+'@example.test', 'role':role,
                                 'organizations':organizations}).status_code == 403
    assert {org['id'] for org in client.get('/v1/catalog').json()['organizations']} == {'general-group','company-common','baiste'}
    user = activate(client, staff_invite, password)
    assert user['role'] == 'user'
    assert client.post('/v1/accounts/invitations', headers={'X-CSRF-Token':user['csrf_token']},
                       json={'email':uuid4().hex+'@example.test','role':'user','organizations':['baiste']}).status_code == 403
    assert client.get('/v1/accounts').status_code == 403


def test_csrf_and_cross_origin_mutations_are_rejected(kb):
    client, db, _ = kb
    email, password = uuid4().hex+'@example.test', 'Fresh-'+uuid4().hex
    bootstrap_superadmin(db, email, password, must_change=False)
    me = login(client, email, password)
    body = {'email':uuid4().hex+'@example.test','role':'user','organizations':['baiste']}
    assert client.post('/v1/accounts/invitations',json=body).status_code == 403
    assert client.post('/v1/accounts/invitations',json=body,headers={
        'X-CSRF-Token':me['csrf_token'],'Origin':'https://evil.invalid',
    }).status_code == 403


def test_last_superadmin_is_protected_and_superadmin_can_invite_another_superadmin(kb):
    client, db, _ = kb
    email, password = uuid4().hex+'@example.test', 'Fresh-'+uuid4().hex
    root = bootstrap_superadmin(db,email,password,must_change=False)
    me = login(client,email,password)
    assert client.patch('/v1/accounts/'+root,json={'role':'user','organizations':['baiste'],'active':True},
                        headers={'X-CSRF-Token':me['csrf_token']}).status_code == 409
    assert client.patch('/v1/accounts/'+root,json={'role':'superadmin','organizations':[],'active':False},
                        headers={'X-CSRF-Token':me['csrf_token']}).status_code == 409
    boss = uuid4().hex+'@example.test'
    invitation = create_invite(client,me['csrf_token'],boss,'superadmin',[])
    assert activate(client,invitation,password)['role'] == 'superadmin'
    assert len(client.get('/v1/catalog').json()['organizations']) == 8
    other = login(client,boss,password)
    assert client.patch('/v1/accounts/'+root,json={'role':'superadmin','organizations':[],'active':False},
                        headers={'X-CSRF-Token':other['csrf_token']}).status_code == 200
    client.cookies.clear()
    assert client.post('/v1/auth/login',json={'email':email,'password':password}).status_code == 401


def test_expired_and_replaced_invitations_are_rejected_and_reset_revokes_old_sessions(kb):
    client, db, _ = kb
    password = 'Fresh-'+uuid4().hex
    root = uuid4().hex+'@example.test'
    bootstrap_superadmin(db,root,password,must_change=False)
    me = login(client,root,password)
    staff = uuid4().hex+'@example.test'
    invitation = create_invite(client,me['csrf_token'],staff)
    with db.connection() as conn:
        principal = conn.execute('SELECT principal_id FROM account_users WHERE email=%s',(staff,)).fetchone()['principal_id']
    replacement = client.post('/v1/accounts/'+principal+'/invitation',headers={'X-CSRF-Token':me['csrf_token']}).json()['invitation']
    assert client.post('/v1/auth/invitation',json={'invitation':invitation}).status_code == 410
    activate(client,replacement,password)
    old_cookie = client.cookies.get('agico_session')
    me = login(client,root,password)
    reset = client.post('/v1/accounts/'+principal+'/invitation',headers={'X-CSRF-Token':me['csrf_token']}).json()['invitation']
    new_password = 'Changed-'+uuid4().hex
    assert activate(client,reset,new_password)['email'] == staff
    client.cookies.set('agico_session',old_cookie,domain='testserver.local',path='/')
    assert client.get('/v1/catalog').status_code == 401
    client.cookies.clear()
    assert client.post('/v1/auth/login',json={'email':staff,'password':password}).status_code == 401
    assert login(client,staff,new_password)['role'] == 'user'
    login(client,root,password)
    another = create_invite(client,client.get('/v1/auth/me').json()['csrf_token'],uuid4().hex+'@example.test')
    with db.connection(write=True) as conn:
        conn.execute("UPDATE account_invitations SET expires_at=now()-interval '1 second' WHERE principal_id IN (SELECT principal_id FROM account_users WHERE status='invited')")
    assert client.post('/v1/auth/invitation',json={'invitation':another}).status_code == 410


def test_failed_logins_are_limited_and_passwords_are_not_returned_or_stored(kb):
    client, db, _ = kb
    email, password = uuid4().hex+'@example.test', 'Fresh-'+uuid4().hex
    bootstrap_superadmin(db,email,password,must_change=False)
    for _ in range(10):
        response = client.post('/v1/auth/login',json={'email':email,'password':'wrong-'+uuid4().hex})
        assert response.status_code == 401
        assert password not in response.text
    assert client.post('/v1/auth/login',json={'email':email,'password':password}).status_code == 429
    with db.connection() as conn:
        row = conn.execute('SELECT password_hash FROM account_users WHERE email=%s',(email,)).fetchone()
        assert password not in row['password_hash']


def test_password_change_cannot_leave_concurrent_old_password_session(kb, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor, TimeoutError
    from threading import Event

    from fastapi.testclient import TestClient

    from agico_kb import accounts

    owner, db, _ = kb
    email, password = uuid4().hex+'@example.test', 'Fresh-'+uuid4().hex
    bootstrap_superadmin(db,email,password,must_change=False)
    me = login(owner,email,password)
    original_verify = accounts.verify_password
    started, release = Event(), Event()
    captured = False

    def controlled_verify(value, digest):
        nonlocal captured
        if not captured:
            captured = True
            started.set()
            assert release.wait(5)
        return original_verify(value,digest)

    monkeypatch.setattr(accounts,'verify_password',controlled_verify)
    old_browser = TestClient(owner.app)
    with ThreadPoolExecutor(max_workers=2) as pool:
        old_login = pool.submit(old_browser.post,'/v1/auth/login',json={'email':email,'password':password})
        assert started.wait(5)
        changed = pool.submit(owner.post,'/v1/auth/change-password',json={
            'current_password':password,'password':'Changed-'+password,'password_confirm':'Changed-'+password},
            headers={'X-CSRF-Token':me['csrf_token']})
        try:
            changed.result(timeout=1)
        except TimeoutError:
            pass  # Account row lock serializes it behind the in-flight login.
        finally:
            release.set()
        assert old_login.result(timeout=5).status_code == 200
        assert changed.result(timeout=5).status_code == 200
    assert old_browser.get('/v1/catalog').status_code == 401
    assert owner.get('/v1/catalog').status_code == 200

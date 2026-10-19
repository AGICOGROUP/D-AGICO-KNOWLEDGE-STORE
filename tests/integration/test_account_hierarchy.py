"""Creator ownership must protect siblings even in the same department."""
from uuid import uuid4

from fastapi.testclient import TestClient

from agico_kb.accounts import bootstrap_superadmin


def setup_accounts(kb):
    root, db, _ = kb
    password = 'Fresh-' + uuid4().hex
    email = uuid4().hex + '@example.test'
    root_id = bootstrap_superadmin(db, email, password, must_change=False)
    me = root.post('/v1/auth/login', json={'email': email, 'password': password}).json()
    root_headers = {'X-CSRF-Token': me['csrf_token']}

    def add(creator, headers, role='user', activate=False):
        response = creator.post('/v1/accounts/invitations', headers=headers, json={
            'email': uuid4().hex + '@example.test', 'role': role, 'organizations': ['baiste'],
        })
        assert response.status_code == 201, response.text
        info = response.json()
        if not activate:
            return info
        browser = TestClient(root.app)
        response = browser.post('/v1/auth/activate', json={
            'invitation': info['invitation'], 'password': password, 'password_confirm': password,
        })
        assert response.status_code == 200, response.text
        return browser, {'X-CSRF-Token': response.json()['csrf_token']}, info

    return root, db, root_headers, root_id, add


def test_manager_controls_only_creator_owned_users_not_department_peers(kb):
    root, db, rh, _, add = setup_accounts(kb)
    manager, mh, manager_info = add(root, rh, 'manager', True)
    peer, ph, _ = add(root, rh, 'manager', True)
    own = add(manager, mh)
    sibling = add(peer, ph)
    root_owned = add(root, rh)
    listed = manager.get('/v1/accounts').json()['items']
    assert {item['principal_id'] for item in listed} == {own['principal_id']}
    for target in (sibling, root_owned):
        path = '/v1/accounts/' + target['principal_id']
        assert manager.post(path + '/invitation', headers=mh).status_code == 403
        assert manager.patch(path, headers=mh, json={
            'role': 'user', 'organizations': ['baiste'], 'active': False,
        }).status_code == 403
    path = '/v1/accounts/' + own['principal_id']
    # A superadmin can resend a link without stealing the creator's child.
    assert root.post(path + '/invitation', headers=rh).status_code == 200
    replacement = manager.post(path + '/invitation', headers=mh)
    assert replacement.status_code == 200
    fresh = TestClient(root.app)
    assert fresh.post('/v1/auth/invitation', json={
        'invitation': replacement.json()['invitation'],
    }).status_code == 200
    assert manager.patch(path, headers=mh, json={
        'role': 'user', 'organizations': ['baiste'], 'active': False,
    }).status_code == 200
    assert manager.patch(path, headers=mh, json={
        'role': 'user', 'organizations': ['baiste'], 'active': True,
    }).status_code == 200
    with db.connection() as conn:
        assert conn.execute('SELECT created_by FROM account_users WHERE principal_id=%s',
            (own['principal_id'],)).fetchone()['created_by'] == manager_info['principal_id']


def test_manager_can_invite_and_manage_own_manager_within_department(kb):
    root, _, rh, _, add = setup_accounts(kb)
    manager, mh, _ = add(root, rh, 'manager', True)
    _, _, peer_info = add(root, rh, 'manager', True)
    child = add(manager, mh, 'manager')

    listed = manager.get('/v1/accounts?tree=true').json()['items']
    assert len(listed) == 1
    assert [item['principal_id'] for item in listed[0]['children']] == [child['principal_id']]
    assert listed[0]['children'][0]['can_manage']
    replacement = manager.post('/v1/accounts/' + child['principal_id'] + '/invitation', headers=mh)
    assert replacement.status_code == 200
    child_browser = TestClient(root.app)
    child_password = 'Fresh-' + uuid4().hex
    activated = child_browser.post('/v1/auth/activate', json={
        'invitation': replacement.json()['invitation'],
        'password': child_password, 'password_confirm': child_password,
    })
    assert activated.status_code == 200
    assert activated.json()['role'] == 'manager'
    assert child_browser.get('/v1/portal-session').json()['publisher_organizations'] == ['baiste']
    grandchild = child_browser.post('/v1/accounts/invitations', headers={
        'X-CSRF-Token': activated.json()['csrf_token'],
    }, json={
        'email': uuid4().hex + '@example.test', 'role': 'user', 'organizations': ['baiste'],
    })
    assert grandchild.status_code == 201
    assert manager.patch('/v1/accounts/' + child['principal_id'], headers=mh, json={
        'role': 'manager', 'organizations': ['baiste'], 'active': True,
    }).status_code == 200
    assert manager.patch('/v1/accounts/' + child['principal_id'], headers=mh, json={
        'role': 'superadmin', 'organizations': ['baiste'], 'active': True,
    }).status_code == 403
    for role, organizations in [('superadmin', ['baiste']), ('manager', ['xingyuan'])]:
        response = manager.post('/v1/accounts/invitations', headers=mh, json={
            'email': uuid4().hex + '@example.test', 'role': role,
            'organizations': organizations,
        })
        assert response.status_code == 403
    assert manager.patch('/v1/accounts/' + peer_info['principal_id'], headers=mh, json={
        'role': 'manager', 'organizations': ['baiste'], 'active': False,
    }).status_code == 403


def test_tree_keeps_superadmins_parallel_and_whole_child_branches_on_one_page(kb):
    root, _, rh, root_id, add = setup_accounts(kb)
    boss = add(root, rh, 'superadmin')
    manager, mh, manager_info = add(root, rh, 'manager', True)
    children = [add(manager, mh) for _ in range(21)]
    first = root.get('/v1/accounts?tree=true&limit=1').json()
    assert first['items'][0]['principal_id'] == root_id
    assert first['has_more'] is True
    branch = first['items'][0]['children']
    assert len(branch) == 1 and branch[0]['principal_id'] == manager_info['principal_id']
    assert {child['principal_id'] for child in branch[0]['children']} == {
        child['principal_id'] for child in children}
    assert all(child['can_manage'] for child in branch[0]['children'])
    assert branch[0]['creator_email'] == first['items'][0]['email']
    second = root.get('/v1/accounts?tree=true&limit=1&offset=1').json()
    assert second['items'][0]['principal_id'] == boss['principal_id']
    assert second['items'][0]['parent_id'] is None
    assert second['has_more'] is False
    own = manager.get('/v1/accounts?tree=true').json()['items']
    assert len(own) == 1 and own[0]['principal_id'] == manager_info['principal_id']
    assert own[0]['can_manage'] is False
    assert len(own[0]['children']) == 21
    assert all(child['can_manage'] for child in own[0]['children'])

from uuid import uuid4

from fastapi.testclient import TestClient

from agico_kb.accounts import bootstrap_superadmin


def test_default_departments_are_readable_without_department_management_or_account_leak(kb):
    root, db, _ = kb
    password, email = 'Fresh-' + uuid4().hex, uuid4().hex + '@example.test'
    bootstrap_superadmin(db, email, password, must_change=False)
    owner = root.post('/v1/auth/login', json={'email': email, 'password': password}).json()
    rh = {'X-CSRF-Token': owner['csrf_token']}

    def account(role, organizations, creator=root, headers=rh):
        made = creator.post('/v1/accounts/invitations', headers=headers, json={
            'email': uuid4().hex + '@example.test', 'role': role, 'organizations': organizations,
        })
        assert made.status_code == 201
        info = made.json()
        browser = TestClient(root.app)
        me = browser.post('/v1/auth/activate', json={
            'invitation': info['invitation'], 'password': password, 'password_confirm': password,
        }).json()
        return browser, {'X-CSRF-Token': me['csrf_token']}, info['principal_id']

    manager, mh, manager_id = account('manager', ['baiste'])
    staff, _, staff_id = account('user', ['baiste'], creator=manager, headers=mh)
    _, _, foreign_id = account('user', ['xingyuan'])
    _, _, global_only_id = account('user', [])
    visible = {'general-group', 'baiste', 'company-common'}
    for browser in (manager, staff):
        catalog = browser.get('/v1/catalog').json()
        assert {org['id'] for org in catalog['organizations']} == visible
        assert set(catalog['default_visible_organizations']) == {'general-group', 'company-common'}
    assert manager.get('/v1/portal-session').json()['publisher_organizations'] == ['baiste']
    assert staff.get('/v1/portal-session').json()['publisher_organizations'] == []
    listed = {item['principal_id'] for item in manager.get('/v1/accounts').json()['items']}
    assert staff_id in listed and foreign_id not in listed and global_only_id not in listed
    for target in (foreign_id, global_only_id):
        assert manager.post('/v1/accounts/' + target + '/invitation', headers=mh).status_code == 403

    for org in ('general-group', 'company-common'):
        content = ('Fresh public ' + uuid4().hex).encode()
        upload = root.post('/v1/uploads', headers={**rh, 'Idempotency-Key': uuid4().hex},
            json={'filename': 'fresh.md', 'size': len(content)}).json()
        assert root.put(upload['upload_url'], headers=rh, content=content).status_code == 200
        record = root.post('/v1/submissions', headers=rh, json={
            'upload_id': upload['upload_id'], 'organization_id': org,
            'title': 'Fresh-' + uuid4().hex, 'visibility': 'department',
        }).json()
        with db.connection(write=True) as conn:
            conn.execute("UPDATE versions SET processing_status='ready' WHERE id=%s", (record['version_id'],))
            conn.execute("UPDATE jobs SET state='complete' WHERE version_id=%s", (record['version_id'],))
        assert manager.post(f"/v1/versions/{record['version_id']}/publish", headers=mh,
            json={'expected_revision': 0}).status_code == 404
        assert root.post(f"/v1/versions/{record['version_id']}/publish", headers=rh,
            json={'expected_revision': 0}).status_code == 200
        for browser in (manager, staff):
            assert browser.get(f"/v1/versions/{record['version_id']}/content").content == content
        assert manager.post(f"/v1/documents/{record['document_id']}/withdraw", headers=mh,
            json={'expected_revision': 1}).status_code == 404

    # Simulate an account registered before defaults existed; no production rows touched.
    with db.connection(write=True) as conn:
        conn.execute('DELETE FROM memberships WHERE principal_id=%s AND organization_id=ANY(%s)',
            (manager_id, ['general-group', 'company-common']))
    assert {org['id'] for org in manager.get('/v1/catalog').json()['organizations']} == visible
    root_list = root.get('/v1/accounts').json()['items']
    old = next(item for item in root_list if item['principal_id'] == manager_id)
    assert {org['id'] for org in old['organizations']} == visible
    # An earlier explicit management assignment is real account data, not a
    # new default grant. Saving the same role must not silently remove it.
    with db.connection(write=True) as conn:
        conn.execute("INSERT INTO memberships VALUES (%s,'general-group','publisher') ON CONFLICT(principal_id,organization_id) DO UPDATE SET role='publisher'", (manager_id,))
    assert 'general-group' in manager.get('/v1/portal-session').json()['publisher_organizations']
    assert root.patch('/v1/accounts/' + manager_id, headers=rh, json={
        'role': 'manager', 'organizations': ['baiste'], 'active': True,
    }).status_code == 200
    with db.connection() as conn:
        assert conn.execute("SELECT role FROM memberships WHERE principal_id=%s AND organization_id='general-group'",
            (manager_id,)).fetchone()['role'] == 'publisher'

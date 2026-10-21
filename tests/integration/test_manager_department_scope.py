from uuid import uuid4

from fastapi.testclient import TestClient

from agico_kb.accounts import bootstrap_superadmin


def test_manager_scope_is_not_bypassed_by_old_ownership_or_explicit_file_share(kb):
    root, db, _ = kb
    password, root_email = 'Fresh-' + uuid4().hex, uuid4().hex + '@example.test'
    bootstrap_superadmin(db, root_email, password, must_change=False)
    owner = root.post('/v1/auth/login', json={'email': root_email, 'password': password}).json()
    rh = {'X-CSRF-Token': owner['csrf_token']}
    email = uuid4().hex + '@example.test'
    invite = root.post('/v1/accounts/invitations', headers=rh, json={
        'email': email, 'role': 'manager', 'organizations': ['baiste', 'xingyuan'],
    }).json()
    manager_id = invite['principal_id']
    manager = TestClient(root.app)
    active = manager.post('/v1/auth/activate', json={
        'invitation': invite['invitation'], 'password': password, 'password_confirm': password,
    }).json()
    mh = {'X-CSRF-Token': active['csrf_token']}

    def upload(browser, headers, published, org='baiste'):
        payload = ('Fresh department material ' + uuid4().hex).encode()
        prepared = browser.post('/v1/uploads', headers={**headers, 'Idempotency-Key': uuid4().hex},
            json={'filename': 'fresh.md', 'size': len(payload)}).json()
        assert browser.put(prepared['upload_url'], content=payload, headers=headers).status_code == 200
        response = browser.post('/v1/submissions', headers=headers, json={
            'upload_id': prepared['upload_id'], 'organization_id': org, 'title': 'Fresh-' + uuid4().hex,
            'visibility': 'private', 'category_id': 'technical', 'base_revision': 0,
        })
        assert response.status_code == 201
        record = response.json()
        if published:
            # Synthetic lifecycle state tests permissions, not extraction accuracy.
            with db.connection(write=True) as conn:
                conn.execute("UPDATE versions SET processing_status='ready' WHERE id=%s", (record['version_id'],))
                conn.execute("UPDATE jobs SET state='complete' WHERE version_id=%s", (record['version_id'],))
            assert root.post(f"/v1/versions/{record['version_id']}/publish", headers=rh,
                json={'expected_revision': 0}).status_code == 200
        return record

    owned = upload(manager, mh, True)
    draft = upload(manager, mh, False)
    shared = upload(root, rh, True)
    allowed = upload(root, rh, True, 'xingyuan')
    assert root.put(f"/v1/documents/{shared['document_id']}/grants", headers=rh,
        json={'expected_revision': 1, 'principal_ids': [manager_id]}).status_code == 200
    for record in (owned, shared, draft):
        assert manager.get(f"/v1/versions/{record['version_id']}/content").status_code == 200
    assert root.patch('/v1/accounts/' + manager_id, headers=rh,
        json={'role': 'manager', 'organizations': ['xingyuan'], 'active': True}).status_code == 200
    assert manager.get('/v1/catalog').status_code == 401
    me = manager.post('/v1/auth/login', json={'email': email, 'password': password}).json()
    mh = {'X-CSRF-Token': me['csrf_token']}
    assert [org['id'] for org in manager.get('/v1/catalog').json()['organizations']] == ['general-group', 'xingyuan', 'company-common']
    visible = manager.get('/v1/documents').json()['items']
    assert {row['version_id'] for row in visible} == {allowed['version_id']}
    assert manager.get('/v1/submissions').json()['total'] == 0
    for record in (owned, shared, draft):
        base = f"/v1/versions/{record['version_id']}"
        for suffix in ('', '/content', '/preview', '/thumbnail'):
            assert manager.get(base + suffix).status_code == 404
        assert root.get(base + '/content').status_code == 200
    for record in (owned, shared):
        result = manager.post('/v1/search', headers=mh,
            json={'mode': 'files', 'document_id': record['document_id'], 'query': ''})
        assert result.status_code == 200 and result.json()['items'] == []
    assert manager.post(f"/v1/submissions/{draft['version_id']}/withdraw", headers=mh).status_code == 404
    assert manager.get(f"/v1/versions/{allowed['version_id']}/content").status_code == 200

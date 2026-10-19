from uuid import uuid4

import pymupdf
from fastapi.testclient import TestClient

from agico_kb.accounts import bootstrap_superadmin


def test_cookie_upload_review_download_and_department_boundaries(kb_large):
    root, db, _ = kb_large
    password = 'Fresh-' + uuid4().hex
    email = uuid4().hex + '@example.test'
    bootstrap_superadmin(db, email, password, must_change=False)
    admin = root.post('/v1/auth/login', json={'email': email, 'password': password}).json()

    def invited(role, org):
        user_email = uuid4().hex + '@example.test'
        response = root.post('/v1/accounts/invitations', json={
            'email': user_email, 'role': role, 'organizations': [org],
        }, headers={'X-CSRF-Token': admin['csrf_token']})
        assert response.status_code == 201
        browser = TestClient(root.app)
        activation = browser.post('/v1/auth/activate', json={
            'invitation': response.json()['invitation'],
            'password': password, 'password_confirm': password,
        })
        assert activation.status_code == 200
        return browser, {'X-CSRF-Token': activation.json()['csrf_token']}

    manager, mh = invited('manager', 'baiste')
    staff, sh = invited('user', 'baiste')
    peer, _ = invited('user', 'baiste')
    foreign, fh = invited('manager', 'xingyuan')
    with pymupdf.open() as document:
        document.new_page().insert_text((72, 72), 'Fresh account file')
        original = document.tobytes()
    prepared = staff.post('/v1/uploads', json={'filename': 'fresh.pdf', 'size': len(original)},
        headers={**sh, 'Idempotency-Key': uuid4().hex})
    assert prepared.status_code == 201
    upload = prepared.json()['upload_id']
    assert staff.put(f'/v1/uploads/{upload}/content', content=original, headers=sh).status_code == 200
    submitted = staff.post('/v1/submissions', headers=sh, json={
        'upload_id': upload, 'organization_id': 'baiste', 'title': 'Fresh account preview',
        'category_id': 'technical', 'visibility': 'department', 'base_revision': 0,
    })
    assert submitted.status_code == 201
    version = submitted.json()['version_id']
    route = f'/v1/versions/{version}'
    assert staff.get(route + '/content').content == original
    assert peer.get(route + '/content').status_code == 404
    assert foreign.get(route + '/content').status_code == 404
    assert manager.get(route + '/preview').status_code in {200, 202}
    # Fresh synthetic lifecycle fixture checks permissions, not parsing accuracy.
    with db.connection(write=True) as conn:
        conn.execute("UPDATE versions SET processing_status='ready' WHERE id=%s", (version,))
        conn.execute("UPDATE jobs SET state='complete' WHERE version_id=%s", (version,))
    assert staff.post(route + '/publish', headers=sh, json={'expected_revision': 0}).status_code in {403, 404}
    assert foreign.post(route + '/publish', headers=fh, json={'expected_revision': 0}).status_code in {403, 404}
    assert manager.post(route + '/publish', json={'expected_revision': 0}).status_code == 403
    assert manager.post(route + '/publish', headers=mh, json={'expected_revision': 0}).status_code == 200
    assert peer.get(route + '/content').content == original
    document_id = submitted.json()['document_id']
    assert manager.post(f'/v1/documents/{document_id}/withdraw', headers=mh,
        json={'expected_revision': 1}).status_code == 200
    assert peer.get(route + '/content').status_code == 404

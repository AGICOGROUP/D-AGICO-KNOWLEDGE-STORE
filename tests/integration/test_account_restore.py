from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg import sql
from psycopg.conninfo import make_conninfo

from agico_kb import operations
from agico_kb.accounts import bootstrap_superadmin
from agico_kb.errors import KBError
from agico_kb.main import create_app


def test_restore_preserves_account_password_role_and_unused_invitation(kb):
    client, db, root = kb
    email, password = uuid4().hex + '@example.test', 'Fresh-' + uuid4().hex
    bootstrap_superadmin(db, email, password, must_change=False)
    owner = client.post('/v1/auth/login', json={'email': email, 'password': password}).json()
    staff = uuid4().hex + '@example.test'
    invitation = client.post('/v1/accounts/invitations', json={
        'email': staff, 'role': 'user', 'organizations': ['baiste'],
    }, headers={'X-CSRF-Token': owner['csrf_token']}).json()['invitation']
    manager_email, manager_password = uuid4().hex + '@example.test', 'Fresh-manager-' + uuid4().hex
    manager_invite = client.post('/v1/accounts/invitations', json={
        'email': manager_email, 'role': 'manager', 'organizations': ['baiste', 'xingyuan'],
    }, headers={'X-CSRF-Token': owner['csrf_token']}).json()
    manager_browser = TestClient(client.app)
    assert manager_browser.post('/v1/auth/activate', json={
        'invitation': manager_invite['invitation'], 'password': manager_password,
        'password_confirm': manager_password,
    }).status_code == 200
    disabled_email, disabled_password = uuid4().hex + '@example.test', 'Fresh-disabled-' + uuid4().hex
    disabled_invite = client.post('/v1/accounts/invitations', json={
        'email': disabled_email, 'role': 'user', 'organizations': ['baiste'],
    }, headers={'X-CSRF-Token': owner['csrf_token']}).json()
    disabled_browser = TestClient(client.app)
    assert disabled_browser.post('/v1/auth/activate', json={
        'invitation': disabled_invite['invitation'], 'password': disabled_password,
        'password_confirm': disabled_password,
    }).status_code == 200
    assert client.patch('/v1/accounts/' + disabled_invite['principal_id'], json={
        'role': 'user', 'organizations': ['baiste'], 'active': False,
    }, headers={'X-CSRF-Token': owner['csrf_token']}).status_code == 200
    archive = root.parent / ('account_backup_' + uuid4().hex)
    feedback = client.post('/v1/feedback', headers={'X-CSRF-Token': owner['csrf_token']}, json={
        'kind':'suggestion','title':'全新迁移反馈','description':'部署后需要保留这条反馈。',
    })
    assert feedback.status_code == 201
    feedback_id = feedback.json()['id']
    binary = Path('.local/postgres/Library/bin').resolve()
    manifest = operations.backup(db.settings, archive, binary)
    assert manifest['migrations'] == [1, 2, 3, 4, 5, 6]
    target = 'restore_test_' + uuid4().hex
    runtime_role = 'test_restore_app_' + uuid4().hex
    runtime_password = uuid4().hex
    try:
        with psycopg.connect(db.settings.database_url, autocommit=True) as conn:
            conn.execute(sql.SQL('CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD {}').format(
                sql.Identifier(runtime_role), sql.Literal(runtime_password)))
        with pytest.raises(ValueError, match='non-administrative'):
            operations.restore(archive, db.settings.database_url, target, root.parent / target, binary,
                               runtime_role='agico_dev')
        assert not (root.parent / target).exists()
        restored = operations.restore(archive, db.settings.database_url, target, root.parent / target, binary,
                                      runtime_role=runtime_role)
        # All recovery login/permissions checks must use the non-super service role.
        restored = replace(restored, database_url=make_conninfo(restored.database_url,
                           user=runtime_role, password=runtime_password))
        with psycopg.connect(restored.database_url) as conn:
            assert conn.execute('SELECT rolsuper,rolcreatedb,rolcreaterole FROM pg_roles WHERE rolname=current_user').fetchone() == (False, False, False)
        with TestClient(create_app(restored)) as recovered:
            assert recovered.get('/v1/auth/settings').json()['enabled']
            assert recovered.get('/v1/catalog', headers={'Authorization': 'Bearer chief'}).status_code == 401
            login = recovered.post('/v1/auth/login', json={'email': email, 'password': password})
            assert login.status_code == 200
            assert login.json()['role'] == 'superadmin'
            assert len(recovered.get('/v1/catalog').json()['organizations']) == 8
            restored_feedback = recovered.get('/v1/feedback').json()['items']
            assert restored_feedback[0]['id'] == feedback_id
            assert restored_feedback[0]['title'] == '全新迁移反馈'
            accounts = recovered.get('/v1/accounts').json()['items']
            manager_account = next(item for item in accounts if item['email'] == manager_email)
            assert manager_account['principal_id'] == manager_invite['principal_id']
            assert manager_account['status'] == 'active' and manager_account['role'] == 'manager'
            assert manager_account['creator_email'] == email
            tree = recovered.get('/v1/accounts?tree=true').json()['items']
            assert len(tree) == 1 and tree[0]['email'] == email
            assert manager_invite['principal_id'] in {item['principal_id'] for item in tree[0]['children']}
            disabled_account = next(item for item in accounts if item['email'] == disabled_email)
            assert disabled_account['principal_id'] == disabled_invite['principal_id']
            assert disabled_account['status'] == 'disabled'
            assert TestClient(recovered.app).post('/v1/auth/login', json={
                'email': disabled_email, 'password': disabled_password,
            }).status_code == 401
            migrated_manager = TestClient(recovered.app)
            manager_login = migrated_manager.post('/v1/auth/login', json={
                'email': manager_email, 'password': manager_password,
            })
            assert manager_login.status_code == 200 and manager_login.json()['role'] == 'manager'
            assert [org['id'] for org in migrated_manager.get('/v1/catalog').json()['organizations']] == ['general-group', 'baiste', 'xingyuan', 'company-common']
            with pytest.raises(KBError, match='不能重复初始化'):
                bootstrap_superadmin(recovered.app.state.db, uuid4().hex + '@example.test', 'Fresh-' + uuid4().hex)
            assert recovered.post('/v1/auth/invitation', json={'invitation': invitation}).json()['email'] == staff
            activation = recovered.post('/v1/auth/activate', json={
                'invitation': invitation, 'password': password, 'password_confirm': password,
            })
            assert activation.status_code == 200
            assert activation.json()['role'] == 'user'
            assert [org['id'] for org in recovered.get('/v1/catalog').json()['organizations']] == ['general-group', 'baiste', 'company-common']
    finally:
        assert target.startswith('restore_test_') and len(target) == 45
        with psycopg.connect(db.settings.database_url, autocommit=True) as conn:
            exists = conn.execute('SELECT 1 FROM pg_database WHERE datname=%s', (target,)).fetchone()
            if exists:
                conn.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(target)))
            conn.execute(sql.SQL('DROP ROLE IF EXISTS {}').format(sql.Identifier(runtime_role)))

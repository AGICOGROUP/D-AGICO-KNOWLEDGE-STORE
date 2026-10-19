from uuid import uuid4

from fastapi.testclient import TestClient

from agico_kb.accounts import bootstrap_superadmin


def feedback_accounts(kb):
    root, db, _ = kb
    password = 'Fresh-'+uuid4().hex
    email = uuid4().hex+'@example.test'
    bootstrap_superadmin(db, email, password, must_change=False)
    me = root.post('/v1/auth/login', json={'email':email,'password':password}).json()
    rh = {'X-CSRF-Token':me['csrf_token']}

    def invite(role):
        name = uuid4().hex+'@example.test'
        made = root.post('/v1/accounts/invitations', headers=rh, json={
            'email':name,'role':role,'organizations':['baiste'],
        }).json()
        browser = TestClient(root.app)
        activated = browser.post('/v1/auth/activate', json={
            'invitation':made['invitation'],'password':password,'password_confirm':password,
        })
        assert activated.status_code == 200
        return browser, {'X-CSRF-Token':activated.json()['csrf_token']}
    user, uh = invite('user')
    manager, mh = invite('manager')
    return root, rh, user, uh, manager, mh


def test_all_roles_submit_feedback_and_only_see_their_own_unless_superadmin(kb):
    root, rh, user, uh, manager, mh = feedback_accounts(kb)
    data = {'kind':'problem','title':'预览加载很慢','description':'打开新合成文件后等待很久。','area':'文件预览'}
    anonymous = TestClient(root.app)
    assert anonymous.post('/v1/feedback', json=data).status_code == 401
    assert user.post('/v1/feedback', json=data).status_code == 403
    for invalid in ({**data,'title':'  '},{**data,'description':'\n '},{**data,'kind':'invalid'}):
        assert user.post('/v1/feedback', headers=uh, json=invalid).status_code == 422
    own = user.post('/v1/feedback', headers=uh, json=data)
    assert own.status_code == 201, own.text
    assert own.json()['status'] == 'new'
    assert own.json()['revision'] == 0
    assert user.post('/v1/feedback', headers={**uh,'Origin':'https://evil.invalid'}, json=data).status_code == 403
    other = manager.post('/v1/feedback', headers=mh, json={**data,'kind':'request','title':'增加一个新的工具'})
    assert other.status_code == 201
    assert root.post('/v1/feedback', headers=rh, json={**data,'kind':'suggestion','title':'简化操作'}) .status_code == 201
    mine = user.get('/v1/feedback?scope=all').json()
    assert [item['id'] for item in mine['items']] == [own.json()['id']]
    assert mine['can_manage'] is False and mine['counts']['total'] == 1
    manager_view = manager.get('/v1/feedback?scope=all').json()
    assert [item['id'] for item in manager_view['items']] == [other.json()['id']]
    assert manager_view['scope'] == 'mine' and manager_view['can_manage'] is False
    assert manager_view['counts']['total'] == 1
    all_feedback = root.get('/v1/feedback').json()
    assert all_feedback['can_manage'] is True and all_feedback['counts']['total'] == 3
    assert len(root.get('/v1/feedback?scope=mine').json()['items']) == 1
    assert len(root.get('/v1/feedback?kind=problem').json()['items']) == 1
    first = root.get('/v1/feedback?limit=1').json()
    assert len(first['items']) == 1 and first['has_more']
    assert len(root.get('/v1/feedback?limit=1&offset=2').json()['items']) == 1


def test_only_superadmin_can_reply_and_change_status_with_conflict_protection(kb):
    root, rh, user, uh, manager, mh = feedback_accounts(kb)
    issue = user.post('/v1/feedback', headers=uh, json={
        'kind':'suggestion','title':'增加预览提示','description':'希望显示文件正在加载。',
    })
    assert issue.status_code == 201, issue.text
    path = '/v1/feedback/'+str(issue.json()['id'])
    update = {'status':'working','reply':'已排查，正在处理。','expected_revision':0}
    for browser, headers in ((user,uh),(manager,mh)):
        assert browser.patch(path, headers=headers, json=update).status_code == 403
    assert root.patch(path, json=update).status_code == 403
    assert root.patch(path, headers=rh, json={**update,'status':'invalid'}).status_code == 422
    response = root.patch(path, headers=rh, json=update)
    assert response.status_code == 200, response.text
    assert response.json()['status'] == 'working' and response.json()['revision'] == 1
    assert root.patch(path, headers=rh, json=update).status_code == 409
    visible = user.get('/v1/feedback?status=working').json()
    assert visible['items'][0]['reply'] == '已排查，正在处理。'
    assert visible['counts']['working'] == 1
    assert root.patch(path, headers=rh, json={**update,'status':'done','expected_revision':1}).status_code == 200
    assert user.get('/v1/feedback?status=new').json()['items'] == []
    assert user.get('/v1/feedback').json()['counts']['done'] == 1
    with kb[1].connection() as conn:
        assert conn.execute('SELECT count(*) AS n FROM feedback_events').fetchone()['n'] == 3

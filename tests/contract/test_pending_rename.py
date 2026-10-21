from conftest import headers, submit


def test_approver_renames_ready_draft_without_changing_source_or_naming_decision(kb):
    client, db, _ = kb
    created, _, content = submit(client, title='公司资料.md', project='项目A')
    version = created.json()['version_id']
    path = f'/v1/versions/{version}/title'
    change = {'current_title': '公司资料.md', 'title': '设备技术说明.md', 'expected_revision': 0}
    with db.connection(write=True) as conn:
        conn.execute("UPDATE versions SET processing_status='ready',capabilities='{"
            "\"naming_review\":{\"state\":\"review\",\"needs_review\":true}}'::jsonb WHERE id=%s", (version,))
        conn.execute("UPDATE jobs SET state='complete' WHERE version_id=%s", (version,))
    for who in ('alice', 'otherchief'):
        assert client.patch(path, headers=headers(who), json=change).status_code == 404
    assert client.patch(path, headers=headers('chief'), json={**change, 'expected_revision': 1}).status_code == 409
    for bad in ('设备技术说明.pdf', '../说明.md', '设备*说明.md', '设备\n说明.md'):
        assert client.patch(path, headers=headers('chief'), json={**change, 'title': bad}).status_code == 422
    response = client.patch(path, headers=headers('chief'), json=change)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result['title'] == '设备技术说明.md' and result['filename'] == '公司资料.md'
    assert result['revision'] == 0 and result['project'] == '项目A'
    assert result['capabilities']['naming_review']['needs_review'] is True
    assert client.patch(path, headers=headers('chief'), json=change).status_code == 409
    assert client.get(f'/v1/versions/{version}/content', headers=headers('chief')).content == content
    assert client.get('/v1/submissions', headers=headers('chief')).json()['items'][0]['title'] == '设备技术说明.md'
    assert client.post(f'/v1/versions/{version}/publish', headers=headers('chief'),
        json={'expected_revision': 0}).status_code == 409  # Rename must not bypass naming review.
    with db.connection() as conn:
        assert conn.execute("SELECT count(*) AS n FROM audit_events WHERE version_id=%s AND action='rename_pending'",
            (version,)).fetchone()['n'] == 1


def test_pending_rename_requires_finished_processing_and_remains_publishable(kb):
    client, db, _ = kb
    created, _, _ = submit(client, title='公司资料.md')
    version = created.json()['version_id']
    path = f'/v1/versions/{version}/title'
    change = {'current_title': '公司资料.md', 'title': '新技术说明.md', 'expected_revision': 0}
    assert client.patch(path, headers=headers('chief'), json=change).status_code == 409
    with db.connection(write=True) as conn:
        conn.execute("UPDATE versions SET processing_status='ready' WHERE id=%s", (version,))
        conn.execute("UPDATE jobs SET state='complete' WHERE version_id=%s", (version,))
    assert client.patch(path, headers=headers('chief'), json=change).status_code == 200
    assert client.post(f'/v1/versions/{version}/publish', headers=headers('chief'),
        json={'expected_revision': 0}).status_code == 200
    assert client.patch(path, headers=headers('chief'), json={**change,
        'current_title': '新技术说明.md', 'title': '再次改名.md', 'expected_revision': 1}).status_code == 409

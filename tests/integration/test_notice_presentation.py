from conftest import headers, submit
from psycopg.types.json import Jsonb


def test_existing_results_use_policy_without_rewriting_diagnostics(kb):
    client, db, _ = kb
    response, _, _ = submit(client)
    vid = response.json()['version_id']
    routine = 'PDF 表格按原页保留；跨页表头和脚注需结合相邻页读取。'
    real_gap = 'PDF 第 3 页数字或符号存在遗漏／冲突，尚未核定；请核对原图。'
    diagnostics = [routine, routine, real_gap]
    with db.connection(write=True) as conn:
        conn.execute('UPDATE versions SET warnings=%s WHERE id=%s', (Jsonb(diagnostics), vid))

    assert client.get(f'/v1/versions/{vid}', headers=headers()).json()['warnings'] == [real_gap]
    pending = client.get('/v1/submissions', headers=headers('chief')).json()['items'][0]
    assert pending['warnings'] == [real_gap]
    review = client.get(f'/v1/versions/{vid}/chunks', headers=headers('chief')).json()
    assert review['warnings'] == [real_gap]
    with db.connection() as conn:
        stored = conn.execute('SELECT warnings FROM versions WHERE id=%s', (vid,)).fetchone()
        assert stored['warnings'] == diagnostics


def test_published_search_and_read_use_same_notice_policy(kb):
    from agico_kb.worker import Worker

    client, db, _ = kb
    response, _, content = submit(client, title='Fresh notice search fixture')
    vid = response.json()['version_id']

    class Embedder:
        dimension = 384
        identity = 'notice-test-only'

        def embed(self, texts):
            return [[0.1] * self.dimension for _ in texts]

    assert Worker(db, client.app.state.settings, embedder=Embedder()).run_once()
    routine = 'PDF 3 页扫描正文采用 OCR 提取；未调用视觉 API。'
    issue = '第 3-4 页解析未完成，该范围请核对原文件。'
    with db.connection(write=True) as conn:
        conn.execute('UPDATE versions SET warnings=%s WHERE id=%s', (Jsonb([routine, issue]), vid))
    published = client.post(f'/v1/versions/{vid}/publish', headers=headers('chief'),
                            json={'expected_revision': 0, 'accept_incomplete': True})
    assert published.status_code == 200
    for query in [{'query': 'Fresh notice search fixture', 'mode': 'files'},
                  {'query': content.decode().split()[-1]}]:
        found = client.post('/v1/search', headers=headers(), json=query).json()
        assert found['items'] and found['items'][0]['warnings'] == [issue]
    read = client.post('/v1/read', headers=headers(), json={'version_id': vid}).json()
    assert read['items'] and read['warnings'] == [issue]
    with db.connection() as conn:
        assert conn.execute('SELECT warnings FROM versions WHERE id=%s', (vid,)).fetchone()['warnings'] == [routine, issue]

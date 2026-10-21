import time
from io import BytesIO
from uuid import uuid4

import pymupdf
from conftest import headers
from PIL import Image


def test_full_document_preview_auth_pages_and_immutable_download(kb_large):
    client, db, _ = kb_large
    with pymupdf.open() as doc:
        for color in [(1, 0, 0), (0, 0, 1)]:
            page = doc.new_page(width=300, height=400)
            page.draw_rect(page.rect, fill=color)
        original = doc.tobytes()
    upload = client.post('/v1/uploads', headers=headers('alice', uuid4().hex),
                         json={'filename':'fresh-two-pages.pdf', 'size':len(original)}).json()
    assert client.put(f"/v1/uploads/{upload['upload_id']}/content", headers=headers(),
                      content=original).status_code == 200
    submitted = client.post('/v1/submissions', headers=headers(), json={
        'upload_id':upload['upload_id'], 'organization_id':'baiste', 'category_id':'technical',
        'title':'Fresh preview', 'visibility':'department', 'base_revision':0,
    })
    assert submitted.status_code == 201
    version = submitted.json()['version_id']
    base = f'/v1/versions/{version}'

    def ready(path, person='alice'):
        response = client.get(path, headers=headers(person))
        deadline = time.monotonic() + 20
        while response.status_code == 202 and time.monotonic() < deadline:
            time.sleep(.05)
            response = client.get(path, headers=headers(person))
        assert response.status_code == 200, response.text[:100]
        assert response.headers['cache-control'] == 'no-store'
        return response

    for endpoint in ['/preview', '/preview/pages/2']:
        assert client.get(base + endpoint).status_code == 401
        assert client.get(base + endpoint, headers=headers('bob')).status_code == 404
        assert client.get(base + endpoint, headers=headers('peer')).status_code == 404
    assert ready(base + '/preview').json() == {'kind':'document', 'pages':2}
    with Image.open(BytesIO(ready(base + '/preview/pages/2', 'chief').content)) as image:
        assert image.getpixel((200, 200)) == (0, 0, 255)
    assert client.get(base + '/preview/pages/3', headers=headers()).status_code == 404
    assert client.get(base + '/preview/pages/0', headers=headers()).status_code == 404
    assert client.get(base + '/preview/pages/2?width=99999', headers=headers()).status_code == 422
    assert client.get(base + '/content', headers=headers()).content == original
    # Synthetic lifecycle state, for permission testing only (no parsing accuracy assertion).
    with db.connection(write=True) as conn:
        conn.execute("UPDATE versions SET processing_status='ready' WHERE id=%s", (version,))
        conn.execute("UPDATE jobs SET state='complete' WHERE version_id=%s", (version,))
    assert client.post(base + '/publish', headers=headers('chief'),
                       json={'expected_revision':0}).status_code == 200
    assert ready(base + '/preview/pages/2', 'peer').status_code == 200
    assert client.get(base + '/preview/pages/2', headers=headers('bob')).status_code == 404
    document = submitted.json()['document_id']
    assert client.post(f'/v1/documents/{document}/withdraw', headers=headers('chief'),
                       json={'expected_revision':1}).status_code == 200
    # Previously cached page must become inaccessible after unpublishing.
    assert client.get(base + '/preview/pages/2', headers=headers('peer')).status_code == 404
    assert client.get('/portal/document-preview.js').status_code == 200

from uuid import uuid4

import pytest
from conftest import headers


@pytest.mark.parametrize('extension',['dwg','dxf'])
def test_cad_waits_for_parser_before_approval(kb, extension):
    client,db,_=kb
    content=b'Fresh CAD library fixture '+uuid4().hex.encode()
    upload=client.post('/v1/uploads',headers=headers(key=uuid4().hex),json={'filename':f'AX-210.{extension}','size':len(content)}).json()['upload_id']
    assert client.put(f'/v1/uploads/{upload}/content',headers=headers(),content=content).status_code==200
    response=client.post('/v1/submissions',headers=headers(),json={'upload_id':upload,'organization_id':'baiste','category_id':'technical','title':'AX-210 equipment drawing','visibility':'department','base_revision':0})
    assert response.status_code==201,response.text
    version=response.json()['version_id']
    assert response.json()['processing_status']=='queued'
    with db.connection() as conn:
        assert conn.execute('SELECT count(*) AS n FROM jobs WHERE version_id=%s',(version,)).fetchone()['n']==1
    published=client.post(f'/v1/versions/{version}/publish',headers=headers('chief'),json={'expected_revision':0,'accept_incomplete':True})
    assert published.status_code==409,published.text
    assert published.json()['error']['code']=='PROCESSING_INCOMPLETE'

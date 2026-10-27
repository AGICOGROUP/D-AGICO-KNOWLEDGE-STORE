"""Temporary provider failures must not permanently request human name review."""
import json
from uuid import uuid4

import httpx

from agico_kb import naming
from agico_kb.worker import Worker
from psycopg.types.json import Jsonb
from tests.conftest import headers


def upload_pending(client):
    filename='SYNTH-'+uuid4().hex+' pump drawing.dwg'
    content=('fresh synthetic naming recovery '+uuid4().hex).encode()
    response=client.post('/v1/uploads',headers=headers(key=uuid4().hex),
                         json={'filename':filename,'size':len(content)})
    upload=response.json()['upload_id']
    assert client.put(f'/v1/uploads/{upload}/content',headers=headers(),content=content).status_code==200
    result=client.post('/v1/submissions',headers=headers(),json={
        'upload_id':upload,'organization_id':'baiste','category_id':'technical',
        'title':filename,'visibility':'department','naming_pending':True})
    assert result.status_code==201
    return result.json()['version_id'],filename


def test_temporary_failure_waits_and_automatically_retries_without_reading_original(kb,monkeypatch):
    client,db,_=kb;settings=client.app.state.settings
    version,filename=upload_pending(client);calls=[]
    def unavailable(*args,**kwargs):
        calls.append(1)
        raise httpx.ReadTimeout('synthetic transport outage')
    monkeypatch.setattr(naming,'_call_model',unavailable)
    def no_original_read(*args):
        raise AssertionError('Do not OCR original while provider is unavailable')
    monkeypatch.setattr(naming,'_bounded_content_hint',no_original_read)
    assert naming.process_pending(db,settings)==1
    before=client.get('/v1/versions/'+version,headers=headers()).json()
    review=before['capabilities']['naming_review']
    assert review['state']=='pending'
    assert review['retryable'] is True and review['attempts']==1 and review['next_attempt_at']
    assert before['title']==filename and before['filename']==filename
    assert Worker(db,settings).claim() is None
    assert naming.process_pending(db,settings)==0 and len(calls)==1
    with db.connection(write=True) as conn:
        conn.execute("UPDATE versions SET capabilities=jsonb_set(capabilities,'{naming_review,next_attempt_at}',to_jsonb((now()-interval '1 second')::text),true) WHERE id=%s",(version,))
    assert naming.process_pending(db,settings)==1
    second=client.get('/v1/versions/'+version,headers=headers()).json()['capabilities']['naming_review']
    assert second['attempts']==2 and len(calls)==2
    from datetime import UTC,datetime
    assert 55 < (datetime.fromisoformat(second['next_attempt_at'])-datetime.now(UTC)).total_seconds() <= 60
    with db.connection(write=True) as conn:
        conn.execute("UPDATE versions SET capabilities=jsonb_set(capabilities,'{naming_review,next_attempt_at}',to_jsonb((now()-interval '1 second')::text),true) WHERE id=%s",(version,))
    def recovered(*args,**kwargs):
        item=naming._fallback(filename,'')
        item.update(title='SYNTH-pump_总装图.dwg',needs_review=False)
        return [item]
    monkeypatch.setattr(naming,'_suggest_batch',recovered)
    assert naming.process_pending(db,settings)==1
    after=client.get('/v1/versions/'+version,headers=headers()).json()
    assert after['capabilities']['naming_review']['state']=='done'
    assert after['filename']==filename
    assert str(Worker(db,settings).claim()['version_id'])==version


def test_legacy_service_review_is_retried_but_genuine_ambiguity_is_not(kb,monkeypatch):
    client,db,_=kb;settings=client.app.state.settings
    failed,filename=upload_pending(client)
    ambiguous,other=upload_pending(client)
    with db.connection(write=True) as conn:
        for version,reason in [(failed,'AI 命名服务暂不可用，请人工核对'),(ambiguous,'无法从文件名或文内线索确定适用对象或项目')]:
            conn.execute("UPDATE versions SET capabilities=jsonb_set(capabilities,'{naming_review}',%s,true) WHERE id=%s",
                         (Jsonb({'state':'review','needs_review':True,'reason':reason,'rules_version':naming.NAMING_RULES_VERSION}),version))
    seen=[]
    def recovered(*args,**kwargs):
        seen.extend(args[4]);item=naming._fallback(filename,'');item.update(needs_review=False)
        return [item]
    monkeypatch.setattr(naming,'_suggest_batch',recovered)
    assert naming.process_pending(db,settings)==1
    assert seen==[filename]
    assert client.get('/v1/versions/'+failed,headers=headers()).json()['capabilities']['naming_review']['state']=='done'
    assert client.get('/v1/versions/'+ambiguous,headers=headers()).json()['capabilities']['naming_review']['state']=='review'
    assert naming.process_pending(db,settings)==0


def test_improved_request_protocol_retries_only_obsolete_service_wait(kb,monkeypatch):
    client,db,_=kb;settings=client.app.state.settings
    version,filename=upload_pending(client)
    with db.connection(write=True) as conn:
        conn.execute("UPDATE versions SET capabilities=jsonb_set(capabilities,'{naming_review}',jsonb_build_object('state','pending','needs_review',true,'retryable',true,'next_attempt_at',now()+interval '20 minutes','attempts',3,'rules_version',%s),true) WHERE id=%s",(naming.NAMING_RULES_VERSION,version))
    def recovered(*args,**kwargs):
        item=naming._fallback(filename,'');item.update(needs_review=False);return [item]
    monkeypatch.setattr(naming,'_suggest_batch',recovered)
    assert naming.process_pending(db,settings)==1
    assert client.get('/v1/versions/'+version,headers=headers()).json()['capabilities']['naming_review']['state']=='done'

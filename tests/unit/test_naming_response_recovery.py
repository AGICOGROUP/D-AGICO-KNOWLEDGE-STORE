import json
from uuid import uuid4

import httpx
import pytest

from agico_kb import naming
from agico_kb.api_keys import replace_key,select_model
from agico_kb.config import Settings


@pytest.mark.parametrize('bad_content,finish_reason',[('{"items":[','length'),('not json','stop')])
def test_incomplete_or_malformed_model_output_uses_next_primary_model(tmp_path,monkeypatch,bad_content,finish_reason):
    settings=Settings('unused',tmp_path/'originals',api_keys_dir=tmp_path/'config/api-keys')
    replace_key(settings,'primary','synthetic-'+uuid4().hex)
    replace_key(settings,'fallback','synthetic-'+uuid4().hex)
    seen=[]
    def post(url,**kwargs):
        model=kwargs['json']['model'];seen.append((url,model));request=httpx.Request('POST',url)
        assert 'bigmodel' in url
        first=model.endswith('flashx')
        return httpx.Response(200,request=request,json={'choices':[{'message':{'content':bad_content if first else '{"items":[]}'},'finish_reason':finish_reason if first else 'stop'}]})
    monkeypatch.setattr(naming.httpx,'post',post)
    assert naming._call_model(settings,['fresh-'+uuid4().hex+'.pdf'],[])=='{"items":[]}'
    assert [model for _,model in seen]==['glm-5.3-flashx','glm-5.3-flash']


def test_portal_distinguishes_service_retry_from_human_review():
    import shutil,subprocess
    from pathlib import Path
    node=shutil.which('node')
    if not node:pytest.skip('Node required for portal status contract')
    source=Path('src/agico_kb/portal/app.js').read_text(encoding='utf-8')
    code=source[source.index('function processingLabel(item) {'):source.index('let pendingRefreshTimer;')]
    script="const input=JSON.parse(require('fs').readFileSync(0,'utf8'));eval(input);const assert=require('assert/strict');assert.equal(processingLabel({naming_review:{state:'pending',retryable:true,needs_review:true}}),'等待命名重试');assert.equal(processingLabel({naming_review:{state:'review',needs_review:true}}),'命名待核对');"
    result=subprocess.run([node,'-e',script],input=json.dumps(code),text=True,encoding='utf-8',capture_output=True,timeout=10)
    assert result.returncode==0,result.stderr


def test_reasoning_request_has_room_for_thought_and_final_json(tmp_path,monkeypatch):
    settings=Settings('unused',tmp_path/'originals',api_keys_dir=tmp_path/'config/api-keys')
    replace_key(settings,'primary','synthetic-'+uuid4().hex)
    seen=[]
    def post(url,**kwargs):
        seen.append(kwargs);request=httpx.Request('POST',url)
        if kwargs['timeout'].read<20:
            raise httpx.ReadTimeout('synthetic reasoning exceeds old 12-second limit',request=request)
        complete=kwargs['json']['max_tokens']>=2048
        return httpx.Response(200,request=request,json={'choices':[{'message':{'content':'{"items":[]}' if complete else ''},'finish_reason':'stop' if complete else 'length'}]})
    monkeypatch.setattr(naming.httpx,'post',post)
    assert naming._call_model(settings,['fresh-'+uuid4().hex+'.pdf'],[])=='{"items":[]}'
    assert len(seen)==1


def test_corrected_request_ignores_old_model_failures_but_preserves_route_interval(tmp_path,monkeypatch):
    from agico_kb import provider_health as health
    settings=Settings('unused',tmp_path/'originals',api_keys_dir=tmp_path/'config/api-keys')
    for route in ('primary','fallback'):
        replace_key(settings,route,'synthetic-'+uuid4().hex)
    clock=[10000.0]
    monkeypatch.setattr(health,'now',lambda:clock[0])
    namespace=health.scope(settings.provider_profile('primary'),naming.API_URL)
    health.failed(settings,namespace,('glm-5.3-flashx','glm-5.3-flash'))
    seen=[]
    def post(url,**kwargs):
        seen.append(url)
        return httpx.Response(200,request=httpx.Request('POST',url),json={'choices':[{'message':{'content':'{"items":[]}'},'finish_reason':'stop'}]})
    monkeypatch.setattr(naming.httpx,'post',post)
    assert naming._call_model(settings,['fresh-'+uuid4().hex+'.pdf'],[])=='{"items":[]}'
    assert 'bigmodel' in seen[-1]
    assert health.start_failover(settings,namespace)==11800
    assert naming._call_model(settings,['fresh-'+uuid4().hex+'.pdf'],[])=='{"items":[]}'
    assert 'cephalon' in seen[-1] and len(seen)==2
    clock[0]=11800
    naming._call_model(settings,['fresh-'+uuid4().hex+'.pdf'],[])
    assert 'bigmodel' in seen[-1] and len(seen)==3


@pytest.mark.parametrize('effort,min_tokens,min_seconds',[('low',4096,30),('medium',8192,60),('high',16384,90)])
def test_naming_output_budget_and_wait_follow_configured_thinking_level(tmp_path,monkeypatch,effort,min_tokens,min_seconds):
    settings=Settings('unused',tmp_path/'originals',api_keys_dir=tmp_path/'config/api-keys')
    replace_key(settings,'primary','synthetic-'+uuid4().hex)
    select_model(settings,'primary','glm-5.3-flash',reasoning_effort=effort)
    seen=[]
    def post(url,**kwargs):
        seen.append(kwargs)
        assert kwargs['json']['max_tokens']>=min_tokens
        assert kwargs['timeout'].read>=min_seconds
        return httpx.Response(200,request=httpx.Request('POST',url),json={'choices':[{'message':{'content':'{"items":[]}'},'finish_reason':'stop'}]})
    monkeypatch.setattr(naming.httpx,'post',post)
    naming._call_model(settings,['fresh-'+uuid4().hex+'.pdf'],[])
    assert len(seen)==1

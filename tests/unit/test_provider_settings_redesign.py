import json
from uuid import uuid4
import httpx
import pytest
from agico_kb.config import Settings


def settings(tmp_path):
    from agico_kb.api_keys import replace_key
    value=Settings('unused',tmp_path/'originals',api_keys_dir=tmp_path/'config/api-keys')
    replace_key(value,'primary','abc-'+uuid4().hex+'9876')
    replace_key(value,'fallback','xyz-'+uuid4().hex+'6543')
    return value


def test_catalog_mask_and_effort_are_exposed_without_the_full_key(tmp_path):
    from agico_kb.api_keys import status
    from agico_kb.vision import _credentials
    s=settings(tmp_path);data=status(s)['profiles']
    assert data['primary']['platform']=='bigmodel.cn'
    assert data['fallback']['platform']=='cephalon.cloud'
    assert data['primary']['models']==['glm-5.3-flashx','glm-5.3-flash']
    assert data['fallback']['models']==['glm-5.3-flashx','glm-5.3-flash','deepseek-v4.1-flash-1017','deepseek-v4.1-flash']
    for name in ('primary','fallback'):
        key,_=_credentials(s.provider_profile(name));mask=data[name]['masked_key']
        assert mask.startswith(key[:3]) and mask.endswith(key[-4:]) and '*' in mask
        assert key not in json.dumps(data)
        assert data[name]['reasoning_effort']=='low'


@pytest.mark.parametrize('effort,native',[('low','low'),('medium','high'),('high','max')])
def test_effort_and_text_model_selection_reach_the_naming_request(tmp_path,monkeypatch,effort,native):
    from agico_kb.api_keys import select_model
    from agico_kb import naming
    from agico_kb.vision import _credentials
    from agico_kb.provider_catalog import reasoning_effort
    s=settings(tmp_path)
    key,_=_credentials(s.provider_profile('fallback'))
    select_model(s,'fallback','deepseek-v4.1-flash-1017',reasoning_effort=effort)
    assert reasoning_effort(s.provider_profile('fallback'))==effort
    seen=[]
    def post(url,**kw):
        seen.append(kw['json'])
        request=httpx.Request('POST',url)
        if 'bigmodel' in url: return httpx.Response(503,request=request)
        return httpx.Response(200,request=request,json={'choices':[{'message':{'content':'{"items":[]}'}}]})
    monkeypatch.setattr(naming.httpx,'post',post)
    assert naming._call_model(s,['fresh-'+uuid4().hex+'.pdf'],[])=='{"items":[]}'
    assert seen[-1]['model']=='deepseek-v4.1-flash-1017'
    assert seen[-1]['reasoning_effort']==native and seen[-1]['thinking']['type']=='enabled'
    assert _credentials(s.provider_profile('fallback'))[0]==key
    assert key not in s.provider_profile('fallback').read_text()


@pytest.mark.parametrize('model',['deepseek-v4.1-flash','deepseek-v4.1-flash-1017'])
def test_selected_fallback_model_receives_the_image_and_visual_effort(tmp_path,monkeypatch,model):
    from agico_kb.api_keys import select_model
    from agico_kb import vision
    s=settings(tmp_path)
    select_model(s,'fallback',model,reasoning_effort='high')
    monkeypatch.setattr(vision,'_png',lambda *a,**kw:b'fresh-png')
    original=httpx.Client;seen=[]
    def respond(request):
        payload=json.loads(request.content);seen.append((str(request.url),payload))
        assert payload['messages'][1]['content'][1]['type']=='image_url'
        if 'bigmodel' in str(request.url): return httpx.Response(503)
        return httpx.Response(200,json={'choices':[{'message':{'content':'fresh 330 mm'},'finish_reason':'stop'}]})
    monkeypatch.setattr(vision.httpx,'Client',lambda **kw:original(transport=httpx.MockTransport(respond),**kw))
    assert vision._describe(b'fresh-data',s,version=uuid4().hex,prompt='read',call_model=vision._call_model,screen=False)=='fresh 330 mm'
    assert seen[-1][1]['model']==model
    assert seen[-1][1]['reasoning_effort']=='max'
    assert len(seen)==3
    from agico_kb.api_keys import status
    assert status(s)['profiles']['fallback']['selected_model']==model
    assert 'text_only_models' not in status(s)['profiles']['fallback']


def test_slider_values_are_validated_and_key_replacement_preserves_preferences(tmp_path):
    from agico_kb.api_keys import select_model,replace_key,status
    s=settings(tmp_path)
    select_model(s,'primary','glm-5.3-flash',reasoning_effort='medium')
    with pytest.raises(ValueError): select_model(s,'primary','glm-5.3-flash',reasoning_effort='ultra')
    with pytest.raises(ValueError): select_model(s,'primary','deepseek-v4.1-flash')
    replace_key(s,'primary','fresh-'+uuid4().hex)
    data=status(s)['profiles']['primary']
    assert data['selected_model']=='glm-5.3-flash' and data['reasoning_effort']=='medium'

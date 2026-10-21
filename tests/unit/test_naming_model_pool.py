import json
from dataclasses import replace

import httpx
import pytest

from agico_kb import naming, vision
from agico_kb.config import Settings


def test_deepseek_is_optional_naming_fallback_without_images(tmp_path, monkeypatch):
    primary=tmp_path/'primary.txt'; fallback=tmp_path/'fallback.txt'
    primary.write_text('api=fresh-primary\nmodel=glm-5.3-flash')
    fallback.write_text('api=fresh-fallback\nmodel=glm-5.3-flashx/glm-5.3-flash')
    settings=Settings('unused',tmp_path,vision_config_file=primary,vision_fallback_config_file=fallback)
    settings=replace(settings,naming_fallback_models=('glm-5.3-flashx','glm-5.3-flash','deepseek-v4-flash'))
    calls=[]
    def post(url,**kwargs):
        payload=kwargs['json']; calls.append(payload['model'])
        assert all(isinstance(message['content'],str) for message in payload['messages'])
        request=httpx.Request('POST',url)
        if payload['model']!='deepseek-v4-flash':
            return httpx.Response(503,request=request)
        return httpx.Response(200,request=request,json={'choices':[{'message':{'content':'{"items":[]}'}}]})
    monkeypatch.setattr(naming.httpx,'post',post)
    assert naming._call_model(settings,['fresh AX-210 manual.pdf'],[])=='{"items":[]}'
    assert calls==['glm-5.3-flash','glm-5.3-flashx','glm-5.3-flash','deepseek-v4-flash']
    assert vision._credential_models(fallback)[1]==('glm-5.3-flashx','glm-5.3-flash')


@pytest.mark.parametrize('model',['deepseek-v4-flash','deepseek-unknown','hy4-preview'])
def test_retired_and_unknown_models_never_receive_images(monkeypatch,model):
    def forbidden(**kwargs):
        raise AssertionError('Image request must not be created')
    monkeypatch.setattr(vision.httpx,'Client',forbidden)
    with pytest.raises(ValueError,match='视觉'):
        vision._call_model('fresh-key',model,b'fresh-image')


def test_hy4_credentials_rejected_in_all_tasks(tmp_path):
    profile=tmp_path/'retired.txt'
    profile.write_text('api=fresh-key\nmodel=hy4-preview')
    with pytest.raises(ValueError,match='允许列表'):
        vision._credentials(profile)


def test_named_pool_loads_from_protected_environment(tmp_path,monkeypatch):
    monkeypatch.setenv('AGICO_KB_DATABASE_URL','unused')
    monkeypatch.setenv('AGICO_KB_STORAGE_ROOT',str(tmp_path))
    monkeypatch.setenv('AGICO_KB_NAMING_FALLBACK_MODELS','glm-5.3-flashx,glm-5.3-flash,deepseek-v4-flash')
    assert Settings.from_env().naming_fallback_models==('glm-5.3-flashx','glm-5.3-flash','deepseek-v4-flash')


def test_hy4_cannot_be_inserted_in_naming_pool(tmp_path,monkeypatch):
    primary=tmp_path/'primary.txt'; fallback=tmp_path/'fallback.txt'
    primary.write_text('api=fresh-primary\nmodel=glm-5.3-flash')
    fallback.write_text('api=fresh-fallback\nmodel=glm-5.3-flashx')
    settings=replace(Settings('unused',tmp_path,vision_config_file=primary,vision_fallback_config_file=fallback),
                     naming_fallback_models=('hy4-preview',))
    def post(url,**kwargs):
        assert json.loads(json.dumps(kwargs['json']))['model']=='glm-5.3-flash'
        raise httpx.ConnectError('Fresh synthetic primary outage')
    monkeypatch.setattr(naming.httpx,'post',post)
    with pytest.raises(ValueError,match='命名'):
        naming._call_model(settings,['fresh.pdf'],[])

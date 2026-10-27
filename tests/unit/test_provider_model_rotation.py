import json
from uuid import uuid4
import httpx
from agico_kb.config import Settings


def managed(tmp_path):
    from agico_kb.api_keys import replace_key
    settings=Settings('unused',tmp_path/'originals',api_keys_dir=tmp_path/'config/api-keys')
    replace_key(settings,'primary','fresh-primary-'+uuid4().hex)
    replace_key(settings,'fallback','fresh-fallback-'+uuid4().hex)
    return settings


def test_encrypted_key_is_readable_only_with_the_server_master(tmp_path):
    from agico_kb.vision import _credentials
    from agico_kb.api_keys import replace_key
    settings=managed(tmp_path)
    secret='fresh-replacement-'+uuid4().hex
    replace_key(settings,'primary',secret)
    path=settings.provider_profile()
    assert secret not in path.read_text()
    assert _credentials(path)[0]==secret
    master=path.parent.parent/'.api-key-encryption.key'
    assert master.is_file()
    master.unlink()
    import pytest
    with pytest.raises(ValueError): _credentials(path)
    with pytest.raises(ValueError): replace_key(settings,'primary','fresh-do-not-reset-master')


def test_saved_model_selection_preserves_secret_and_reorders_only_approved_models(tmp_path):
    from agico_kb.api_keys import select_model,status
    from agico_kb.vision import _credential_models
    import pytest
    settings=managed(tmp_path)
    before=_credential_models(settings.provider_profile('fallback'))[0]
    select_model(settings,'fallback','glm-5.3-flash')
    key,models=_credential_models(settings.provider_profile('fallback'))
    assert key==before and models==('glm-5.3-flash','glm-5.3-flashx','deepseek-v4.1-flash-1017','deepseek-v4.1-flash')
    state=status(settings)['profiles']['fallback']
    assert state['selected_model']=='glm-5.3-flash'
    assert state['models']==['glm-5.3-flashx','glm-5.3-flash','deepseek-v4.1-flash-1017','deepseek-v4.1-flash']
    with pytest.raises(ValueError): select_model(settings,'fallback','deepseek-v4-flash')
    with pytest.raises(ValueError): select_model(settings,'fallback','hy4-preview')
    assert _credential_models(settings.provider_profile('fallback'))[0]==before


def test_visual_failure_switches_for_exactly_half_an_hour_across_calls(tmp_path,monkeypatch):
    from agico_kb import vision,provider_health
    settings=managed(tmp_path)
    clock=[10000.0]
    monkeypatch.setattr(provider_health,'now',lambda:clock[0])
    monkeypatch.setattr(vision,'_png',lambda blob,**kw:b'fresh-png')
    original=httpx.Client
    calls=[]
    healthy=False
    def respond(request):
        nonlocal healthy
        model=json.loads(request.content)['model']
        route='fallback' if 'cephalon' in str(request.url) else 'primary'
        calls.append((route,model))
        if route=='primary' or model=='glm-5.3-flashx':
            if not healthy: return httpx.Response(503)
        return httpx.Response(200,json={'choices':[{'message':{'content':'visible 330 mm'},'finish_reason':'stop'}]})
    monkeypatch.setattr(vision.httpx,'Client',lambda **kw:original(transport=httpx.MockTransport(respond),**kw))
    for index in range(2):
        assert vision._describe(b'unused',settings,version=str(index),prompt='read',call_model=vision._call_model,screen=False)=='visible 330 mm'
        healthy=True
    assert calls==[('primary','glm-5.3-flashx'),('primary','glm-5.3-flash'),('fallback','glm-5.3-flashx'),('fallback','glm-5.3-flash'),('fallback','glm-5.3-flash')]
    clock[0]+=1799
    assert vision._describe(b'unused',settings,version='near-expiry',prompt='read',call_model=vision._call_model,screen=False)=='visible 330 mm'
    assert calls[-1]==('fallback','glm-5.3-flash')
    clock[0]+=1
    assert vision._describe(b'unused',settings,version='expired',prompt='read',call_model=vision._call_model,screen=False)=='visible 330 mm'
    assert calls[-1]==('primary','glm-5.3-flashx')
    from agico_kb.api_keys import status
    assert status(settings)['profiles']['fallback']['selected_model']=='glm-5.3-flashx'


def test_naming_obeys_selection_and_persistent_cooldown(tmp_path,monkeypatch):
    from agico_kb import naming,provider_health
    from agico_kb.api_keys import select_model
    settings=managed(tmp_path)
    select_model(settings,'fallback','glm-5.3-flash')
    clock=[20000.0];monkeypatch.setattr(provider_health,'now',lambda:clock[0])
    calls=[]
    def post(url,**kw):
        model=kw['json']['model'];calls.append((url,model))
        request=httpx.Request('POST',url)
        if 'bigmodel' in url: return httpx.Response(503,request=request)
        return httpx.Response(200,request=request,json={'choices':[{'message':{'content':'{"items":[]}'}}]})
    monkeypatch.setattr(naming.httpx,'post',post)
    for _ in range(2): assert naming._call_model(settings,['fresh-document.pdf'],[])=='{"items":[]}'
    assert [model for _,model in calls]==['glm-5.3-flashx','glm-5.3-flash','glm-5.3-flash','glm-5.3-flash']
    assert 'bigmodel' not in calls[-1][0]
    clock[0]+=1800
    naming._call_model(settings,['another-fresh-document.pdf'],[])
    assert 'bigmodel' in calls[-3][0]


def test_all_models_failing_pause_without_repeated_network_calls(tmp_path,monkeypatch):
    from agico_kb import vision,provider_health
    from agico_kb.api_keys import status
    import pytest
    settings=managed(tmp_path)
    monkeypatch.setattr(provider_health,'now',lambda:30000.0)
    monkeypatch.setattr(vision,'_png',lambda blob,**kw:b'fresh-png')
    original=httpx.Client;calls=[]
    def respond(request):
        calls.append(str(request.url))
        return httpx.Response(503)
    monkeypatch.setattr(vision.httpx,'Client',lambda **kw:original(transport=httpx.MockTransport(respond),**kw))
    for index in range(2):
        with pytest.raises(vision.VisionDeferred) as error:
            vision._describe(b'fresh-data',settings,version=str(index),prompt='read',call_model=vision._call_model,screen=False)
        assert error.value.delay_seconds==1800
    assert len(calls)==6
    data=status(settings)['profiles']
    assert data['primary']['active_model'] is None
    assert data['fallback']['temporary_until'] is not None


def test_tampered_ciphertext_is_rejected_and_new_key_resets_old_cooldown(tmp_path,monkeypatch):
    from agico_kb.api_keys import replace_key,status
    from agico_kb.vision import _credentials
    from agico_kb import provider_health
    import pytest
    settings=managed(tmp_path)
    profile=settings.provider_profile()
    ns=provider_health.scope(profile,'https://open.bigmodel.cn/api/paas/v4/chat/completions')
    provider_health.failed(settings,ns,('glm-5.3-flashx',))
    assert status(settings)['profiles']['primary']['temporary_until']
    replace_key(settings,'primary','fresh-reset-'+uuid4().hex)
    assert status(settings)['profiles']['primary']['temporary_until'] is None
    data=profile.read_text()
    token=data.splitlines()[0]
    profile.write_text(token[:-6]+'xxxxxx'+chr(10)+'model=glm-5.3-flash')
    with pytest.raises(ValueError): _credentials(profile)


def test_encrypted_configuration_is_portable_with_external_material(tmp_path):
    import shutil
    from agico_kb.vision import _credential_models
    settings=managed(tmp_path/'source')
    before=_credential_models(settings.provider_profile('fallback'))
    target=tmp_path/'new-server/config'
    shutil.copytree(settings.provider_keys_directory.parent,target)
    assert _credential_models(target/'api-keys/fallback.txt')==before
    assert before[0] not in (target/'api-keys/fallback.txt').read_text()


def test_cooldown_is_read_by_an_independent_worker_process(tmp_path):
    import subprocess,sys
    from agico_kb import provider_health
    settings=managed(tmp_path)
    path=settings.provider_profile('fallback')
    namespace=provider_health.scope(path,'fixed-endpoint')
    provider_health.failed(settings,namespace,('glm-5.3-flashx',))
    code="from pathlib import Path; from agico_kb.config import Settings; from agico_kb.provider_health import cooldowns; import sys; assert 'glm-5.3-flashx' in cooldowns(Settings('unused',Path(sys.argv[1])),sys.argv[2])"
    result=subprocess.run([sys.executable,'-c',code,str(settings.storage_root),namespace],capture_output=True,timeout=15)
    assert result.returncode==0

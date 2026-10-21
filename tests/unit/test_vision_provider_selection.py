import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from io import BytesIO

import httpx
import pytest
from PIL import Image

from agico_kb.config import Settings
from agico_kb.vision import _call_model, _credentials, _describe

OFFICIAL = 'https://open.bigmodel.cn/api/paas/v4/chat/completions'
CEPHALON = 'https://cephalon.cloud/user-center/v1/model/chat/completions'


def image_bytes():
    stream = BytesIO()
    Image.new('RGB', (80, 50), 'white').save(stream, format='PNG')
    return stream.getvalue()


def configs(tmp_path, models='glm-5.3-flash'):
    primary = tmp_path/'primary.txt'
    backup = tmp_path/'approved-backup.txt'
    primary.write_text('api=primary-test-secret\nmodel=glm-5.3-flash')
    backup.write_text('api=backup-test-secret\nmodel='+models)
    return Settings('unused', tmp_path/'storage', vision_config_file=primary,
                    vision_fallback_config_file=backup, vision_fallback_model=models.split('/')[0],
                    vision_fallback_api_url=CEPHALON)


@pytest.mark.parametrize('model', ['deepseek-v4-flash','glm-5.3-flashx'])
def test_supplied_models_can_be_read_when_explicitly_configured(tmp_path, model):
    config = tmp_path/'credential.txt'
    config.write_text('api=fresh-test-secret\nmodel='+model)
    assert _credentials(config) == ('fresh-test-secret',model)


def test_same_model_at_two_providers_keeps_primary_and_cache_separate(tmp_path, monkeypatch):
    settings = configs(tmp_path)
    monkeypatch.setattr('agico_kb.vision.API_URL', OFFICIAL)
    monkeypatch.setattr('agico_kb.vision.time.sleep', lambda _:None)
    original = httpx.Client
    calls = []
    healthy = False
    def response(request):
        calls.append((str(request.url),request.headers['authorization']))
        if str(request.url)==OFFICIAL and not healthy:
            return httpx.Response(503)
        text = 'official 330 mm' if str(request.url)==OFFICIAL else 'backup 330 mm'
        return httpx.Response(200,json={'choices':[{'message':{'content':text},'finish_reason':'stop'}]})
    monkeypatch.setattr('agico_kb.vision.httpx.Client',lambda **kwargs:original(transport=httpx.MockTransport(response),**kwargs))
    for expected in ('backup 330 mm','official 330 mm'):
        assert _describe(image_bytes(),settings,version='fresh-provider-test',prompt='read',
                         call_model=_call_model,screen=False,model_override='glm-5.3-flash')==expected
        healthy = True
    assert calls == [(OFFICIAL,'Bearer primary-test-secret')]*2 + [
        (CEPHALON,'Bearer backup-test-secret'),(OFFICIAL,'Bearer primary-test-secret')]


def test_only_approved_backup_models_are_used(tmp_path, monkeypatch):
    settings = configs(tmp_path,'glm-5.3-flashx/glm-5.3-flash')
    monkeypatch.setattr('agico_kb.vision.API_URL', OFFICIAL)
    monkeypatch.setattr('agico_kb.vision.time.sleep', lambda _:None)
    original = httpx.Client
    calls=[]
    def response(request):
        model=json.loads(request.content)['model']
        calls.append((str(request.url),model))
        if str(request.url)==OFFICIAL or model=='glm-5.3-flashx':
            return httpx.Response(503)
        return httpx.Response(200,json={'choices':[{'message':{'content':'330 mm'},'finish_reason':'stop'}]})
    monkeypatch.setattr('agico_kb.vision.httpx.Client',lambda **kwargs:original(transport=httpx.MockTransport(response),**kwargs))
    assert _describe(image_bytes(),settings,version='fresh-pool-test',prompt='read',call_model=_call_model,screen=False)=='330 mm'
    assert calls == [(OFFICIAL,'glm-5.3-flash')]*2 + [(CEPHALON,'glm-5.3-flashx')]*2 + [(CEPHALON,'glm-5.3-flash')]
    assert not any(model in {'deepseek-v4-flash','hy4-preview'} for _,model in calls)


def test_changing_backup_profile_does_not_change_primary(tmp_path):
    settings = configs(tmp_path,'glm-5.3-flashx')
    assert _credentials(settings.vision_config_file)[1]=='glm-5.3-flash'
    assert _credentials(settings.vision_fallback_config_file)[1]=='glm-5.3-flashx'
    assert replace(settings,vision_fallback_config_file=None).vision_config_file==settings.vision_config_file


def test_cephalon_requests_share_one_provider_slot(monkeypatch):
    original = httpx.Client
    active=peak=0
    lock=threading.Lock()
    def response(request):
        nonlocal active,peak
        with lock:
            active+=1; peak=max(peak,active)
        time.sleep(.03)
        with lock:
            active-=1
        return httpx.Response(200,json={'choices':[{'message':{'content':'330 mm'},'finish_reason':'stop'}]})
    monkeypatch.setattr('agico_kb.vision.httpx.Client',lambda **kwargs:original(transport=httpx.MockTransport(response),**kwargs))
    with ThreadPoolExecutor(max_workers=4) as pool:
        results=list(pool.map(lambda _: _call_model('test','glm-5.3-flash',image_bytes(),api_url=CEPHALON),range(4)))
    assert results==['330 mm']*4
    assert peak==1


@pytest.mark.parametrize('kind', ['word','excel'])
def test_document_entry_does_not_store_backup_result_as_primary(tmp_path, monkeypatch, kind):
    from docx import Document
    from docx.shared import Cm

    from agico_kb import vision
    from agico_kb.excel_visuals import ExcelPicture
    from agico_kb.ingestion import Parsed
    settings=configs(tmp_path)
    monkeypatch.setattr('agico_kb.vision.API_URL',OFFICIAL)
    monkeypatch.setattr('agico_kb.vision._has_image_text',lambda _:True)
    monkeypatch.setattr('agico_kb.vision.time.sleep',lambda _:None)
    original=httpx.Client
    healthy=False
    def response(request):
        if str(request.url)==OFFICIAL and not healthy:
            return httpx.Response(503)
        text='official 330 mm' if str(request.url)==OFFICIAL else 'backup 330 mm'
        return httpx.Response(200,json={'choices':[{'message':{'content':text},'finish_reason':'stop'}]})
    monkeypatch.setattr('agico_kb.vision.httpx.Client',lambda **kwargs:original(transport=httpx.MockTransport(response),**kwargs))
    path=tmp_path/'new.docx'
    if kind=='word':
        picture=tmp_path/'new.png'; picture.write_bytes(image_bytes())
        document=Document(); document.add_picture(str(picture),width=Cm(12)); document.save(path)
        run=lambda:vision.enrich_word(Parsed(),path,settings,call_model=vision._call_model)
    else:
        picture=ExcelPicture('Fresh','A1',image_bytes())
        monkeypatch.setattr('agico_kb.vision.extract_pictures',lambda _: [picture])
        monkeypatch.setattr('agico_kb.vision.visual_counts',lambda _: {'pictures':1})
        run=lambda:vision.enrich_excel(Parsed(),path,settings,call_model=vision._call_model,ocr_text=lambda _: 'source text')
    first=run()
    assert any('backup 330 mm' in chunk.text for chunk in first.chunks)
    healthy=True
    second=run()
    assert any('official 330 mm' in chunk.text for chunk in second.chunks)

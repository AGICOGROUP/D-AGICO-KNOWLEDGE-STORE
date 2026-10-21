import json
import os
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import httpx

from agico_kb import naming, vision
from agico_kb.config import Settings

ENDPOINT='https://cephalon.cloud/user-center/v1/model/chat/completions'


def test_provider_slot_serializes_fresh_processes(tmp_path):
    script=tmp_path/'child.py'
    script.write_text('''import json,sys,time
from pathlib import Path
from agico_kb.provider_limits import provider_slot
root=Path(sys.argv[1]); name=sys.argv[2]
(root/(name+'.ready')).touch()
while not (root/'go').exists(): time.sleep(.01)
with provider_slot("https://cephalon.cloud/user-center/v1/model/chat/completions"):
    start=time.monotonic(); time.sleep(.15); end=time.monotonic()
print(json.dumps({'start':start,'end':end}))
''',encoding='utf-8')
    env={**os.environ,'AGICO_KB_STORAGE_ROOT':str(tmp_path)}
    children=[subprocess.Popen([sys.executable,str(script),str(tmp_path),name],env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
              for name in ('api','worker')]
    try:
        deadline=time.monotonic()+10
        while not all((tmp_path/(name+'.ready')).exists() for name in ('api','worker')):
            if time.monotonic()>deadline:
                raise AssertionError('Fresh children failed to become ready')
            time.sleep(.01)
        (tmp_path/'go').touch()
        rows=[]
        for child in children:
            stdout,stderr=child.communicate(timeout=10)
            assert child.returncode==0,stderr
            rows.append(json.loads(stdout))
        rows.sort(key=lambda row:row['start'])
        assert rows[0]['end']<=rows[1]['start']
    finally:
        for child in children:
            if child.poll() is None:
                child.terminate(); child.communicate(timeout=10)


def test_naming_and_visual_requests_share_provider_gate(tmp_path,monkeypatch):
    monkeypatch.setenv('AGICO_KB_STORAGE_ROOT',str(tmp_path))
    primary=tmp_path/'primary.txt'; backup=tmp_path/'backup.txt'
    primary.write_text('api=test-primary\nmodel=glm-5.3-flash')
    backup.write_text('api=test-backup\nmodel=glm-5.3-flashx')
    cfg=Settings('unused',tmp_path,vision_config_file=primary,vision_fallback_config_file=backup,
                 vision_fallback_api_url=ENDPOINT)
    official='https://official.example.test/chat/completions'
    monkeypatch.setattr(naming,'API_URL',official)
    active=peak=0; lock=threading.Lock()
    def handler(request):
        nonlocal active,peak
        if str(request.url)==official:
            return httpx.Response(503)
        with lock:
            active+=1; peak=max(active,peak)
        time.sleep(.1)
        with lock:
            active-=1
        return httpx.Response(200,json={'choices':[{'message':{'content':'{"items":[]}'},'finish_reason':'stop'}]})
    original=httpx.Client
    monkeypatch.setattr(vision.httpx,'Client',lambda **kwargs:original(transport=httpx.MockTransport(handler),**kwargs))
    def post(url,**kwargs):
        with original(transport=httpx.MockTransport(handler)) as client:
            return client.post(url,**kwargs)
    monkeypatch.setattr(naming.httpx,'post',post)
    with ThreadPoolExecutor(max_workers=2) as pool:
        one=pool.submit(naming._call_model,cfg,['new.pdf'],[])
        two=pool.submit(vision._call_model,'test-backup','glm-5.3-flashx',b'fresh-png',api_url=ENDPOINT)
        assert one.result()=='{"items":[]}'
        assert two.result()=='{"items":[]}'
    assert peak==1

"""Route failover is a single 30-minute interval, not staggered model timers."""
import json
from uuid import uuid4

import httpx
import pytest

from agico_kb.config import Settings


def setup_route(tmp_path, monkeypatch, kind, respond):
    from agico_kb import vision, naming
    from agico_kb.api_keys import replace_key
    settings = Settings('unused', tmp_path/'originals', api_keys_dir=tmp_path/'config/api-keys')
    for name in ('primary', 'fallback'):
        replace_key(settings, name, 'synthetic-'+uuid4().hex)
    monkeypatch.setattr(vision, '_png', lambda *a, **kw: b'fresh-png')
    client = httpx.Client
    monkeypatch.setattr(vision.httpx, 'Client', lambda **kw: client(transport=httpx.MockTransport(respond), **kw))
    def post(url, **kw):
        return respond(httpx.Request('POST', url, json=kw['json']))
    monkeypatch.setattr(naming.httpx, 'post', post)
    def run(task=kind):
        if task == 'vision':
            return vision._describe(b'fresh-data', settings, version=uuid4().hex, prompt='read',
                                    call_model=vision._call_model, screen=False)
        return naming._call_model(settings, ['fresh-'+uuid4().hex+'.pdf'], [])
    return settings, run


@pytest.mark.parametrize('kind', ['vision', 'naming'])
def test_primary_second_model_success_never_uses_fallback(tmp_path, monkeypatch, kind):
    calls = []
    def respond(request):
        model = json.loads(request.content)['model']
        route = 'primary' if 'bigmodel' in str(request.url) else 'fallback'
        calls.append((route, model))
        assert route == 'primary'
        return httpx.Response(503 if model.endswith('flashx') else 200, request=request,
                              json={'choices':[{'message':{'content':'{"items":[]}'},'finish_reason':'stop'}]})
    _, run = setup_route(tmp_path, monkeypatch, kind, respond)
    assert run() == '{"items":[]}'
    assert calls == [('primary','glm-5.3-flashx'), ('primary','glm-5.3-flash')]


@pytest.mark.parametrize('kind', ['vision', 'naming'])
def test_failover_waits_full_interval_after_last_primary_failure_and_is_shared(tmp_path, monkeypatch, kind):
    from agico_kb import provider_health
    from agico_kb.api_keys import status
    clock = [10000.0]
    monkeypatch.setattr(provider_health, 'now', lambda: clock[0])
    calls = []
    primary_healthy = False
    def respond(request):
        model = json.loads(request.content)['model']
        route = 'primary' if 'bigmodel' in str(request.url) else 'fallback'
        calls.append((route, model))
        if route == 'primary' and not primary_healthy:
            if model == 'glm-5.3-flash':
                clock[0] += 60
            return httpx.Response(503, request=request)
        return httpx.Response(200, request=request,
                              json={'choices':[{'message':{'content':'{"items":[]}'},'finish_reason':'stop'}]})
    settings, run = setup_route(tmp_path, monkeypatch, kind, respond)
    assert run() == '{"items":[]}'
    assert calls[:3] == [('primary','glm-5.3-flashx'), ('primary','glm-5.3-flash'), ('fallback','glm-5.3-flashx')]
    primary_healthy = True
    clock[0] = 11800  # First model's timer expires; route switch still has a minute left.
    assert run('naming' if kind == 'vision' else 'vision') == '{"items":[]}'
    assert calls[-1][0] == 'fallback'
    assert len(calls) == 4
    assert status(settings)['profiles']['primary']['active_model'] is None
    clock[0] = 11859
    assert run() == '{"items":[]}'
    assert calls[-1][0] == 'fallback'
    clock[0] = 11860
    assert run() == '{"items":[]}'
    assert calls[-1] == ('primary','glm-5.3-flashx')


def test_route_interval_is_persistent_and_not_extended_by_other_workers(tmp_path, monkeypatch):
    from agico_kb import provider_health as health
    import subprocess, sys
    settings = Settings('unused', tmp_path/'originals')
    clock = [20000.0]
    monkeypatch.setattr(health, 'now', lambda: clock[0])
    assert health.start_failover(settings, 'primary-scope') == 21800
    clock[0] += 60
    assert health.start_failover(settings, 'primary-scope') == 21800
    code = "from pathlib import Path; import sys; from agico_kb.config import Settings; from agico_kb import provider_health as h; h.now=lambda:21000; assert h.failover_until(Settings('unused',Path(sys.argv[1])),'primary-scope')==21800"
    result = subprocess.run([sys.executable,'-c',code,str(settings.storage_root)],capture_output=True,timeout=15)
    assert result.returncode == 0
    clock[0] = 21800
    assert health.failover_until(settings, 'primary-scope') is None
    assert health.start_failover(settings, 'primary-scope') == 23600

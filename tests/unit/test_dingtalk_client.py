import json

import httpx
import pytest

from agico_kb.dingtalk_client import DingTalkClient, DingTalkConfig
from agico_kb.errors import KBError


def config(tmp_path, **changes):
    path = tmp_path / 'ding.json'
    value = dict(corp_id='ding-test', app_key='test-key', app_secret='fresh-test-secret',
                 agent_id='123', department_map={'10': ['baiste']},
                 maintenance_principals=['fixture-admin'])
    value.update(changes)
    path.write_text(json.dumps(value), encoding='utf-8')
    return DingTalkConfig.read(path)


def test_code_exchange_uses_app_token_and_caches_only_token(tmp_path):
    calls = []
    def respond(request):
        calls.append(request.url.path)
        if request.url.path == '/gettoken':
            return httpx.Response(200, json={'errcode': 0, 'access_token': 'new-token', 'expires_in': 7200})
        assert request.url.params['access_token'] == 'new-token'
        if request.url.path.endswith('getuserinfo'):
            assert json.loads(request.content)['code'] == 'fresh-code'
            return httpx.Response(200, json={'errcode': 0, 'result': {'userid': 'u1'}})
        return httpx.Response(200, json={'errcode': 0, 'result': {
            'userid': 'u1', 'name': '合成员工', 'active': True, 'dept_id_list': [10]}})
    client = DingTalkClient(config(tmp_path), transport=httpx.MockTransport(respond))
    assert client.identity('fresh-code').user_id == 'u1'
    assert client.employee('u1').department_ids == ('10',)
    assert calls.count('/gettoken') == 1
    assert calls.count('/topapi/v2/user/get') == 2


@pytest.mark.parametrize('response', [
    httpx.Response(200, text='not json'),
    httpx.Response(200, json={'errcode': 0}),
    httpx.Response(200, json={'errcode': 40029, 'errmsg': 'fresh-test-secret'}),
    httpx.Response(500, text='fresh-test-secret'),
])
def test_provider_failure_sanitized(tmp_path, response):
    client = DingTalkClient(config(tmp_path), transport=httpx.MockTransport(lambda _: response))
    with pytest.raises(KBError) as error:
        client.identity('sensitive-code')
    assert 'fresh-test-secret' not in str(error.value.payload)
    assert 'sensitive-code' not in str(error.value.payload)


def test_timeout_sanitized(tmp_path):
    def respond(request):
        raise httpx.ReadTimeout('sensitive-code', request=request)
    with pytest.raises(KBError):
        DingTalkClient(config(tmp_path), transport=httpx.MockTransport(respond)).identity('code')


@pytest.mark.parametrize('changes', [
    {'refresh_seconds': 301}, {'refresh_seconds': True}, {'department_map': {'0': ['baiste']}},
    {'department_map': {'10': ['does-not-exist']}}, {'app_secret': ''},
    {'api_url': 'https://evil.invalid'}, {'maintenance_principals': 'all'},
])
def test_invalid_config_fails_closed(tmp_path, changes):
    with pytest.raises(ValueError):
        config(tmp_path, **changes)


def test_department_descendants_expand_from_real_hierarchy(tmp_path):
    cfg = config(tmp_path, department_map={'10': ['baiste']})
    client = DingTalkClient(cfg)
    # Explicit roots inherit only verified child relationships.
    assert client.organizations(('12',), [
        {'dept_id': 10, 'parent_id': 1}, {'dept_id': 11, 'parent_id': 10},
        {'dept_id': 12, 'parent_id': 11}, {'dept_id': 20, 'parent_id': 1},
    ]) == {'baiste'}
    assert client.organizations(('20',), []) == set()


def test_application_scope_is_checked_for_the_configured_agent(tmp_path):
    from agico_kb.dingtalk_client import Employee
    def respond(request):
        if request.url.path == '/gettoken':
            return httpx.Response(200, json={'errcode': 0, 'access_token': 'token', 'expires_in': 7200})
        assert request.url.host == 'oapi.dingtalk.com'
        assert request.url.path == '/microapp/list_by_userid'
        assert request.url.params['access_token'] == 'token'
        return httpx.Response(200, json={'errcode': 0, 'appList': [{'agentId': 123}] if request.url.params['userid'] == 'u1' else []})
    client = DingTalkClient(config(tmp_path), transport=httpx.MockTransport(respond))
    assert client.visible(Employee('u1', '合成', ('11',), True), [{'dept_id': 11, 'parent_id': 10}])
    assert not client.visible(Employee('u2', '合成', ('20',), True), [])


def test_rejected_cached_token_is_replaced_once(tmp_path):
    tokens = 0
    def respond(request):
        nonlocal tokens
        if request.url.path == '/gettoken':
            tokens += 1
            return httpx.Response(200, json={'errcode': 0, 'access_token': 'token-' + str(tokens), 'expires_in': 7200})
        if request.url.params['access_token'] == 'token-1':
            return httpx.Response(200, json={'errcode': 40014, 'errmsg': 'invalid token'})
        return httpx.Response(200, json={'errcode': 0, 'result': {'userid': 'u1', 'name': '合成', 'active': True, 'dept_id_list': [10]}})
    client = DingTalkClient(config(tmp_path), transport=httpx.MockTransport(respond))
    assert client.employee('u1').user_id == 'u1'
    assert tokens == 2


def test_confirmed_absent_employee_has_distinct_safe_error(tmp_path):
    def respond(request):
        if request.url.path == '/gettoken':
            return httpx.Response(200, json={'errcode': 0, 'access_token': 'token', 'expires_in': 7200})
        return httpx.Response(200, json={'errcode': 60121, 'errmsg': 'private employee detail'})
    with pytest.raises(KBError) as error:
        DingTalkClient(config(tmp_path), transport=httpx.MockTransport(respond)).employee('u1')
    assert error.value.payload['error']['code'] == 'DINGTALK_IDENTITY_REMOVED'


def test_http_logging_never_exposes_application_secret_or_access_token(tmp_path, caplog):
    import logging
    caplog.set_level(logging.INFO, logger='httpx')
    def respond(request):
        if request.url.path == '/gettoken':
            return httpx.Response(200, json={'errcode': 0, 'access_token': 'private-test-token', 'expires_in': 7200})
        return httpx.Response(200, json={'errcode': 0, 'result': {
            'userid': 'u1', 'name': '合成', 'active': True, 'dept_id_list': [10]}})
    client = DingTalkClient(config(tmp_path), transport=httpx.MockTransport(respond))
    try:
        client.employee('u1')
    finally:
        client.close()
    assert 'fresh-test-secret' not in caplog.text
    assert 'private-test-token' not in caplog.text

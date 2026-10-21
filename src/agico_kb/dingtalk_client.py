"""Bounded enterprise H5 authentication and read-only directory calls."""
import json
import logging
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from .errors import KBError


class _CredentialLogFilter(logging.Filter):
    """HTTPX logs full request URLs at INFO; legacy DingTalk uses query credentials."""
    def filter(self, record):
        message = record.getMessage()
        if 'https://oapi.dingtalk.com/' in message:
            record.msg = re.sub(r'(https://oapi\.dingtalk\.com/[^\s?\"\']+)\?[^\s\"\']*',
                                r'\1?[redacted]', message)
            record.args = ()
        return True


_http_logger = logging.getLogger('httpx')
if not any(isinstance(item, _CredentialLogFilter) for item in _http_logger.filters):
    _http_logger.addFilter(_CredentialLogFilter())


@dataclass(frozen=True)
class DingTalkConfig:
    corp_id: str
    app_key: str
    app_secret: str = field(repr=False)
    agent_id: str = ''
    department_map: dict = field(default_factory=dict)
    maintenance_principals: tuple = ()
    refresh_seconds: int = 300

    @classmethod
    def read(cls, path):
        value = json.loads(Path(path).read_text(encoding='utf-8-sig'))
        if not isinstance(value, dict) or set(value) - set(cls.__dataclass_fields__):
            raise ValueError('Invalid DingTalk configuration fields')
        for key in ('corp_id', 'app_key', 'app_secret', 'agent_id'):
            if not isinstance(value.get(key), str) or not value[key].strip():
                raise ValueError('Missing DingTalk configuration field: ' + key)
        if not re.fullmatch(r'[1-9][0-9]*', value['agent_id']):
            raise ValueError('Invalid application AgentId')
        ttl = value.get('refresh_seconds', 300)
        if type(ttl) is not int or not 30 <= ttl <= 300:
            raise ValueError('DingTalk identity refresh must be between 30 and 300 seconds')
        mapping = value.get('department_map', {})
        known = json.loads(Path(__file__).with_name('taxonomy.json').read_text(encoding='utf-8'))['organizations']
        if not isinstance(mapping, dict):
            raise ValueError('Invalid DingTalk department mapping')
        for dept, organizations in mapping.items():
            if not re.fullmatch(r'[1-9][0-9]*', dept) or not isinstance(organizations, list) or not organizations:
                raise ValueError('Invalid DingTalk department mapping')
            if any(not isinstance(org, str) or org not in known for org in organizations):
                raise ValueError('Unknown knowledge base department')
        maintenance = value.get('maintenance_principals', [])
        if not isinstance(maintenance, list) or any(not isinstance(v, str) or not v for v in maintenance):
            raise ValueError('Invalid maintenance principals')
        return cls(**{**value, 'maintenance_principals': tuple(maintenance)})


@dataclass(frozen=True)
class Employee:
    user_id: str
    name: str
    department_ids: tuple[str, ...]
    active: bool
    union_id: str = ''


class DingTalkClient:
    def __init__(self, config, *, transport=None):
        self.config = config
        self.http = httpx.Client(timeout=httpx.Timeout(10, connect=5), transport=transport,
                                 follow_redirects=False, trust_env=False)
        self._lock = threading.Lock()
        self._token, self._expires = '', 0

    def close(self):
        self.http.close()

    def _request(self, method, path, **kwargs):
        try:
            response = self.http.request(method, 'https://oapi.dingtalk.com' + path, **kwargs)
            response.raise_for_status()
            data = response.json()
        except (httpx.HTTPError, ValueError):
            raise KBError('DINGTALK_UNAVAILABLE', '钉钉身份服务暂不可用，请稍后重试。', 503) from None
        if not isinstance(data, dict):
            raise KBError('DINGTALK_RESPONSE', '钉钉返回的信息不完整，请联系开发人员。', 503)
        if type(data.get('errcode')) is not int:
            raise KBError('DINGTALK_RESPONSE', '钉钉返回的信息不完整，请联系开发人员。', 503)
        if data['errcode'] in {40014, 42001} and path != '/gettoken':
            raise KBError('DINGTALK_TOKEN_INVALID', '钉钉应用凭证已失效。', 503)
        if data['errcode'] in {60121, 60111} and path == '/topapi/v2/user/get':
            raise KBError('DINGTALK_IDENTITY_REMOVED', '该员工不在当前企业的有效通讯录中。', 403)
        if data['errcode'] != 0:
            # Deliberately do not echo upstream messages or request URLs containing credentials.
            raise KBError('DINGTALK_REJECTED', '钉钉未能确认当前身份或应用权限，请重新进入应用。', 403)
        return data

    def _access_token(self):
        with self._lock:
            if self._expires > time.monotonic():
                return self._token
            data = self._request('GET', '/gettoken', params={
                'appkey': self.config.app_key, 'appsecret': self.config.app_secret})
            token, lifetime = data.get('access_token'), data.get('expires_in')
            if not isinstance(token, str) or not token or type(lifetime) is not int or lifetime < 120:
                raise KBError('DINGTALK_RESPONSE', '钉钉凭证信息不完整。', 503)
            self._token, self._expires = token, time.monotonic() + min(lifetime - 60, 7100)
            return token

    def _authorized(self, method, path, *, body=None, params=None):
        for attempt in range(2):
            token = self._access_token()
            try:
                data = self._request(method, path, params={**(params or {}), 'access_token': token},
                                     **({'json': body} if body is not None else {}))
                break
            except KBError as exc:
                if exc.payload['error']['code'] != 'DINGTALK_TOKEN_INVALID' or attempt:
                    raise
                with self._lock:
                    if self._token == token:
                        self._token, self._expires = '', 0
        return data

    def _post(self, path, body):
        data = self._authorized('POST', path, body=body)
        if 'result' not in data:
            raise KBError('DINGTALK_RESPONSE', '钉钉返回的信息不完整。', 503)
        return data['result']

    def visible(self, employee, hierarchy):
        # Contact visibility is distinct from the employee's permission to use this app.
        data = self._authorized('GET', '/microapp/list_by_userid', params={'userid': employee.user_id})
        apps = data.get('appList')
        if not isinstance(apps, list):
            raise KBError('DINGTALK_SCOPE_UNKNOWN', '无法核对员工可用应用，请检查应用列表读取权限。', 503)
        agents = []
        for app in apps:
            agent = app.get('agentId') if isinstance(app, dict) else None
            if type(agent) not in (str, int) or not re.fullmatch(r'[1-9][0-9]*', str(agent)):
                raise KBError('DINGTALK_SCOPE_UNKNOWN', '员工可用应用信息不完整。', 503)
            agents.append(str(agent))
        return self.config.agent_id in agents

    def identity(self, code):
        result = self._post('/topapi/v2/user/getuserinfo', {'code': code})
        if not isinstance(result, dict) or not isinstance(result.get('userid'), str) or not result['userid']:
            raise KBError('DINGTALK_RESPONSE', '钉钉未返回有效员工身份。', 503)
        # Code exchange is performed with this company's app token, never a client-supplied CorpId.
        return self.employee(result['userid'])

    def employee(self, user_id):
        result = self._post('/topapi/v2/user/get', {'userid': user_id, 'language': 'zh_CN'})
        if not isinstance(result, dict) or result.get('userid') != user_id or not isinstance(result.get('name'), str):
            raise KBError('DINGTALK_RESPONSE', '钉钉员工信息不完整。', 503)
        departments = result.get('dept_id_list')
        if not isinstance(departments, list) or any(type(d) is not int or d < 1 for d in departments):
            raise KBError('DINGTALK_RESPONSE', '钉钉部门信息不完整。', 503)
        if type(result.get('active')) is not bool:
            raise KBError('DINGTALK_RESPONSE', '钉钉员工状态不完整。', 503)
        union = result.get('unionid', '')
        if not isinstance(union, str):
            raise KBError('DINGTALK_RESPONSE', '钉钉员工标识不完整。', 503)
        return Employee(user_id, result['name'][:200], tuple(str(d) for d in departments), result['active'], union)

    def departments(self):
        pending, seen, result = [1], set(), []
        while pending:
            parent = pending.pop()
            if parent in seen or len(seen) >= 1000:
                raise KBError('DINGTALK_DIRECTORY', '部门结构异常或超过本次同步范围。', 503)
            seen.add(parent)
            children = self._post('/topapi/v2/department/listsub', {'dept_id': parent, 'language': 'zh_CN'})
            if not isinstance(children, list):
                raise KBError('DINGTALK_DIRECTORY', '部门信息不完整，未更新权限。', 503)
            for child in children:
                if not isinstance(child, dict) or type(child.get('dept_id')) is not int or child['dept_id'] < 1 or child.get('parent_id') != parent:
                    raise KBError('DINGTALK_DIRECTORY', '部门信息不完整，未更新权限。', 503)
                result.append({'dept_id': child['dept_id'], 'parent_id': parent, 'name': str(child.get('name', ''))[:200]})
                pending.append(child['dept_id'])
        return result

    def organizations(self, departments, hierarchy):
        parents = {str(d['dept_id']): str(d['parent_id']) for d in hierarchy}
        organizations = set()
        for dept in departments:
            visited = set()
            while dept and dept not in visited:
                visited.add(dept)
                organizations.update(self.config.department_map.get(dept, []))
                dept = parents.get(dept)
        return organizations

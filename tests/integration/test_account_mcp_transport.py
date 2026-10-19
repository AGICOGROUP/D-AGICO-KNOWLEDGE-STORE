import asyncio
import base64
import hashlib
import json
import socket
import time
from functools import partial
from urllib.parse import parse_qs, urlencode, urlsplit
from uuid import uuid4

import httpx
import httpx2
import uvicorn
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

from agico_kb.accounts import bootstrap_superadmin


def test_account_oauth_real_mcp_initialize_tools_and_catalog(kb):
    client, db, _ = kb
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    sock.listen(128)
    sock.setblocking(False)
    port = sock.getsockname()[1]
    base = f'http://127.0.0.1:{port}'
    resource = base + '/mcp'
    server = uvicorn.Server(uvicorn.Config(client.app, log_level='error', lifespan='off'))
    future = client.portal.start_task_soon(partial(server.serve, sockets=[sock]))
    try:
        for _ in range(100):
            if server.started:
                break
            time.sleep(0.01)
        assert server.started
        email, password = uuid4().hex + '@example.test', 'Fresh-' + uuid4().hex
        bootstrap_superadmin(db, email, password, must_change=False)
        with httpx.Client(base_url=base) as browser:
            me = browser.post('/v1/auth/login', json={'email': email, 'password': password}).json()
            callback = 'http://127.0.0.1:19876/callback'
            registration = browser.post('/register', json={
                'client_name': 'Fresh real transport client', 'redirect_uris': [callback],
                'token_endpoint_auth_method': 'none',
                'grant_types': ['authorization_code', 'refresh_token'], 'response_types': ['code'],
            })
            assert registration.status_code == 201
            identity = registration.json()['client_id']
            verifier = uuid4().hex + uuid4().hex
            challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
            authorization = browser.get('/authorize?' + urlencode({
                'client_id': identity, 'redirect_uri': callback, 'response_type': 'code',
                'code_challenge': challenge, 'code_challenge_method': 'S256', 'scope': 'kb:access',
                'resource': resource, 'state': 'new-transport-state',
            }))
            request_id = parse_qs(urlsplit(authorization.headers['location']).query)['oauth'][0]
            consent = browser.post('/v1/auth/oauth-consent',
                json={'request': request_id, 'approved': True}, headers={'X-CSRF-Token': me['csrf_token']})
            assert consent.status_code == 200
            code = parse_qs(urlsplit(consent.json()['redirect']).query)['code'][0]
            issued = browser.post('/token', data={
                'grant_type': 'authorization_code', 'client_id': identity, 'code': code,
                'redirect_uri': callback, 'code_verifier': verifier, 'resource': resource,
            })
            assert issued.status_code == 200
            access = issued.json()['access_token']
            browser.cookies.clear()
            assert browser.get('/v1/accounts', headers={'Authorization': 'Bearer ' + access}).status_code == 401

        async def scenario():
            async with (
                httpx2.AsyncClient(headers={'Authorization': 'Bearer ' + access}) as http,
                Client(streamable_http_client(resource, http_client=http), mode='legacy') as agent,
            ):
                tools = await agent.list_tools()
                assert {'kb_catalog', 'kb_search', 'kb_read', 'kb_publish'} <= {tool.name for tool in tools.tools}
                catalog = await agent.call_tool('kb_catalog', {})
                assert not catalog.is_error
                data = catalog.structured_content or json.loads(catalog.content[0].text)
                assert len(data['organizations']) == 8

        asyncio.run(scenario())
    finally:
        server.should_exit = True
        future.result(timeout=10)
        sock.close()

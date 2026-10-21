"""MCP SDK OAuth endpoints backed by personal-account consent and durable grants."""
import fnmatch
import json
import secrets
from contextvars import ContextVar
from datetime import UTC, datetime, timedelta
from functools import partial, wraps
from urllib.parse import parse_qs, urlencode, urlsplit
from uuid import uuid4

from anyio import to_thread
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    AuthorizeError,
    RefreshToken,
    RegistrationError,
    TokenError,
)
from mcp.server.auth.routes import create_auth_routes
from mcp.server.auth.settings import ClientRegistrationOptions, RevocationOptions
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from pydantic import AnyHttpUrl, Field
from starlette.requests import Request as StarletteRequest
from starlette.routing import Route

from . import accounts
from .auth import token_hash
from .errors import KBError
from .dingtalk_identity import ensure_current

_context = ContextVar('account_oauth_context', default=None)


def asynchronous(function):
    @wraps(function)
    async def wrapped(*args, **kwargs):
        return await to_thread.run_sync(partial(function, *args, **kwargs))
    return wrapped


class Consent(accounts.Body):
    request: str = Field(min_length=20, max_length=128, repr=False)
    approved: bool


class Provider:
    def __init__(self, db):
        self.db = db

    def refresh_credential_identity(self, secret, *, code=False):
        if not getattr(self.db, 'dingtalk', None):
            return True
        with self.db.connection() as conn:
            if code:
                row = conn.execute('SELECT principal_id FROM oauth_codes WHERE code_hash=%s AND used_at IS NULL AND expires_at>now()',
                                   (token_hash(secret),)).fetchone()
            else:
                row = conn.execute('''SELECT g.principal_id FROM oauth_credentials c JOIN oauth_grants g ON g.id=c.grant_id
                    WHERE c.secret_hash=%s AND NOT g.revoked AND c.expires_at>now()''', (token_hash(secret),)).fetchone()
        return bool(row and ensure_current(self.db, row['principal_id']))

    @asynchronous
    def get_client(self, client_id):
        with self.db.connection() as conn:
            row = conn.execute('SELECT metadata FROM oauth_clients WHERE client_id=%s', (client_id,)).fetchone()
            return OAuthClientInformationFull.model_validate(row['metadata']) if row else None

    @asynchronous
    def register_client(self, client_info):
        if client_info.token_endpoint_auth_method != 'none':
            raise RegistrationError('invalid_client_metadata', 'Use a public PKCE client.')
        for value in client_info.redirect_uris or []:
            url = urlsplit(str(value))
            if url.fragment or url.username or url.password or not (url.scheme == 'https' or
                    (url.scheme == 'http' and url.hostname in {'127.0.0.1','localhost','::1'})):
                raise RegistrationError('invalid_redirect_uri', 'HTTPS or loopback HTTP callback required.')
        with self.db.connection(write=True) as conn:
            conn.execute('INSERT INTO oauth_clients(client_id,name,redirect_uris,metadata) VALUES (%s,%s,%s::jsonb,%s::jsonb)',
                         (client_info.client_id, (client_info.client_name or 'AI assistant')[:200],
                          json.dumps([str(uri) for uri in client_info.redirect_uris or []]), client_info.model_dump_json()))

    @asynchronous
    def authorize(self, client, params):
        origin = _context.get()['origin']
        if params.resource != origin+'/mcp':
            raise AuthorizeError('invalid_target', 'The resource must identify this MCP endpoint.')
        if params.scopes and set(params.scopes) != {'kb:access'}:
            raise AuthorizeError('invalid_scope', 'Only kb:access is supported.')
        request_id = secrets.token_urlsafe(32)
        with self.db.connection(write=True) as conn:
            conn.execute('INSERT INTO oauth_pending(request_hash,client_id,parameters) VALUES (%s,%s,%s::jsonb)',
                         (token_hash(request_id), client.client_id, params.model_dump_json()))
        return origin+'/?'+urlencode({'oauth':request_id})

    @asynchronous
    def load_authorization_code(self, client, authorization_code):
        if not self.refresh_credential_identity(authorization_code, code=True):
            return None
        with self.db.connection() as conn:
            row = conn.execute('SELECT * FROM oauth_codes WHERE code_hash=%s AND client_id=%s AND used_at IS NULL AND expires_at>now()',
                               (token_hash(authorization_code), client.client_id)).fetchone()
            principal = accounts.load_principal(conn,row['principal_id']) if row else None
            if not principal or principal.must_change_password:
                return None
            return AuthorizationCode(code=authorization_code, scopes=['kb:access'], expires_at=row['expires_at'].timestamp(),
                client_id=row['client_id'], code_challenge=row['challenge'], redirect_uri=row['redirect_uri'],
                redirect_uri_provided_explicitly=row['redirect_explicit'], resource=row['resource'], subject=row['principal_id'])

    def mint(self, conn, grant_id):
        access, refresh = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        for secret, kind, life in [(access,'access',3600), (refresh,'refresh',30*86400)]:
            conn.execute('INSERT INTO oauth_credentials(secret_hash,grant_id,kind,expires_at) VALUES (%s,%s,%s,%s)',
                         (token_hash(secret),grant_id,kind,datetime.now(UTC)+timedelta(seconds=life)))
        return OAuthToken(access_token=access, token_type='Bearer', expires_in=3600,
                          refresh_token=refresh, scope='kb:access')

    @asynchronous
    def exchange_authorization_code(self, client, authorization_code):
        if not ensure_current(self.db, authorization_code.subject):
            raise TokenError('invalid_grant', 'Corporate identity is no longer valid.')
        if _context.get().get('resource') != authorization_code.resource:
            raise TokenError('invalid_target', 'Resource does not match the authorization.')
        issued = None
        with self.db.connection(write=True) as conn:
            # Account lock precedes credential/grant locks in every issuing transaction,
            # so password or permission changes cannot race a newly issued grant.
            principal = accounts.load_principal(conn,authorization_code.subject,lock=True)
            if principal and not principal.must_change_password:
                row = conn.execute('UPDATE oauth_codes SET used_at=now() WHERE code_hash=%s AND client_id=%s AND used_at IS NULL AND expires_at>now() RETURNING *',
                                   (token_hash(authorization_code.code),client.client_id)).fetchone()
                if row and row['principal_id'] == principal.id:
                    grant_id = uuid4()
                    conn.execute('INSERT INTO oauth_grants(id,principal_id,client_id,resource) VALUES (%s,%s,%s,%s)',
                                 (grant_id,row['principal_id'],client.client_id,row['resource']))
                    issued = self.mint(conn,grant_id)
        # SDK protocol errors are frozen dataclasses; raise outside generator
        # context managers, which otherwise attempt to mutate their traceback.
        if issued is None:
            raise TokenError('invalid_grant', 'Authorization is expired or already used.')
        return issued

    @asynchronous
    def load_refresh_token(self, client, refresh_token):
        if not self.refresh_credential_identity(refresh_token):
            return None
        with self.db.connection(write=True) as conn:
            row = conn.execute("""SELECT c.*,g.principal_id,g.client_id,g.resource,g.revoked FROM oauth_credentials c
                JOIN oauth_grants g ON g.id=c.grant_id WHERE c.secret_hash=%s AND c.kind='refresh'
                AND g.client_id=%s""", (token_hash(refresh_token),client.client_id)).fetchone()
            if row and row['used_at']:
                # Commit family revocation before returning a rejected replay.
                conn.execute('UPDATE oauth_grants SET revoked=true WHERE id=%s', (row['grant_id'],))
                return None
            if not row or row['revoked'] or row['expires_at'] <= datetime.now(UTC):
                return None
            principal = accounts.load_principal(conn,row['principal_id'])
            if not principal or principal.must_change_password:
                return None
            return RefreshToken(token=refresh_token,client_id=client.client_id,scopes=['kb:access'],
                                expires_at=int(row['expires_at'].timestamp()),resource=row['resource'],subject=principal.id)

    @asynchronous
    def exchange_refresh_token(self, client, refresh_token, scopes):
        if not ensure_current(self.db, refresh_token.subject):
            raise TokenError('invalid_grant', 'Corporate identity is no longer valid.')
        if _context.get().get('resource') != refresh_token.resource:
            raise TokenError('invalid_target','Resource does not match the authorization.')
        if set(scopes) - {'kb:access'}:
            raise TokenError('invalid_scope','Cannot increase scope.')
        issued = None
        with self.db.connection(write=True) as conn:
            principal = accounts.load_principal(conn,refresh_token.subject,lock=True)
            row = conn.execute("""SELECT c.*,g.principal_id,g.revoked FROM oauth_credentials c
                JOIN oauth_grants g ON c.grant_id=g.id WHERE c.secret_hash=%s AND g.client_id=%s
                AND c.kind='refresh' FOR UPDATE OF c,g""",
                (token_hash(refresh_token.token),client.client_id)).fetchone()
            if row and row['used_at']:
                conn.execute('UPDATE oauth_grants SET revoked=true WHERE id=%s', (row['grant_id'],))
            elif row and not row['revoked'] and row['expires_at']>datetime.now(UTC) and principal and not principal.must_change_password and row['principal_id']==principal.id:
                conn.execute('UPDATE oauth_credentials SET used_at=now() WHERE secret_hash=%s', (row['secret_hash'],))
                conn.execute("UPDATE oauth_credentials SET expires_at=now() WHERE grant_id=%s AND kind='access'",(row['grant_id'],))
                issued = self.mint(conn,row['grant_id'])
        # Reuse revocation must commit even when two loads preceded the first exchange.
        if issued is None:
            raise TokenError('invalid_grant','Authorization is expired or already used.')
        return issued

    @asynchronous
    def load_access_token(self, token):
        if not self.refresh_credential_identity(token):
            return None
        with self.db.connection() as conn:
            row = conn.execute("""SELECT c.*,g.principal_id,g.client_id,g.resource FROM oauth_credentials c
                JOIN oauth_grants g ON g.id=c.grant_id WHERE c.secret_hash=%s AND c.kind='access'
                AND c.expires_at>now() AND NOT g.revoked""",(token_hash(token),)).fetchone()
            principal = accounts.load_principal(conn,row['principal_id']) if row else None
            if not principal or principal.must_change_password:
                return None
            return AccessToken(token=token,client_id=row['client_id'],scopes=['kb:access'],
                               expires_at=int(row['expires_at'].timestamp()),resource=row['resource'],subject=principal.id)

    @asynchronous
    def revoke_token(self, token):
        with self.db.connection(write=True) as conn:
            conn.execute('UPDATE oauth_grants SET revoked=true WHERE id IN (SELECT grant_id FROM oauth_credentials WHERE secret_hash=%s)',
                         (token_hash(token.token),))


class OAuthEndpoint:
    def __init__(self, db, settings, path):
        self.db, self.settings, self.path = db, settings, path
        self.provider = Provider(db)
        self.cache = {}

    async def __call__(self, scope, receive, send):
        request = StarletteRequest(scope, receive)
        origin = str(request.base_url).rstrip('/')
        host = request.headers.get('host','')
        if not any(fnmatch.fnmatchcase(host,pattern) for pattern in self.settings.mcp_allowed_hosts):
            await JSONResponse({'error':'invalid_request'},status_code=400)(scope,receive,send)
            return
        if not await to_thread.run_sync(accounts.enabled,self.db):
            await JSONResponse({'error':'authorization_not_enabled'},status_code=404)(scope,receive,send)
            return
        context = {'origin':origin}
        if self.path == '/token' and scope['method'] == 'POST':
            chunks, length = [], 0
            async for part in request.stream():
                length += len(part)
                if length > 16384:
                    await JSONResponse({'error':'invalid_request'},status_code=413)(scope,receive,send)
                    return
                chunks.append(part)
            body = b''.join(chunks)
            try:
                values = parse_qs(body.decode('utf-8',errors='replace'),max_num_fields=30)
            except ValueError:
                await JSONResponse({'error':'invalid_request'},status_code=400)(scope,receive,send)
                return
            if len(values.get('resource',[])) != 1:
                await JSONResponse({'error':'invalid_target'},status_code=400)(scope,receive,send)
                return
            context['resource'] = values['resource'][0]
            async def replay():
                return {'type':'http.request','body':body,'more_body':False}
            receive = replay
        if origin not in self.cache:
            try:
                routes = create_auth_routes(self.provider,AnyHttpUrl(origin),
                    client_registration_options=ClientRegistrationOptions(enabled=True,valid_scopes=['kb:access'],default_scopes=['kb:access']),
                    revocation_options=RevocationOptions(enabled=True))
            except ValueError:
                await JSONResponse({'error':'invalid_request'},status_code=400)(scope,receive,send)
                return
            if len(self.cache) >= 8:
                self.cache.clear()
            self.cache[origin] = {route.path:route.app for route in routes}
        marker = _context.set(context)
        try:
            await self.cache[origin][self.path](scope,receive,send)
        finally:
            _context.reset(marker)


def routes(db, settings):
    return [Route(path,OAuthEndpoint(db,settings,path),methods=methods) for path,methods in [
        ('/.well-known/oauth-authorization-server',['GET','OPTIONS']),
        ('/authorize',['GET','POST']),('/token',['POST','OPTIONS']),
        ('/register',['POST','OPTIONS']),('/revoke',['POST','OPTIONS'])]]


def router(db):
    result = APIRouter()

    @result.get('/.well-known/oauth-protected-resource')
    @result.get('/.well-known/oauth-protected-resource/mcp')
    def protected_resource(request: Request):
        origin = str(request.base_url).rstrip('/')
        return JSONResponse({'resource':origin+'/mcp','authorization_servers':[origin],
                             'scopes_supported':['kb:access'],'bearer_methods_supported':['header']},
                            headers={'Cache-Control':'no-store'})

    def pending(conn, secret):
        row = conn.execute("""SELECT p.*,c.name FROM oauth_pending p JOIN oauth_clients c ON c.client_id=p.client_id
            WHERE p.request_hash=%s AND p.used_at IS NULL AND p.expires_at>now() FOR UPDATE OF p""",(token_hash(secret),)).fetchone()
        if not row:
            raise KBError('OAUTH_EXPIRED','AI 授权请求已过期或已处理，请从客户端重新连接。',410)
        return row

    @result.get('/v1/auth/oauth-request/{request_id}')
    def consent_info(request_id: str, request: Request):
        with db.connection() as conn:
            row = pending(conn,request_id)
            if row['parameters']['resource'] != str(request.base_url).rstrip('/')+'/mcp':
                raise KBError('OAUTH_ORIGIN','请从原知识库地址完成授权。',400)
            return {'client_name':row['name'],'scope':'kb:access'}

    @result.post('/v1/auth/oauth-consent')
    def consent(body: Consent, request: Request):
        with db.connection(write=True) as conn:
            principal = accounts.require_current_session(conn,request)
            row = pending(conn,body.request)
            params = AuthorizationParams.model_validate(row['parameters'])
            if params.resource != str(request.base_url).rstrip('/')+'/mcp':
                raise KBError('OAUTH_ORIGIN','请从原知识库地址完成授权。',400)
            conn.execute('UPDATE oauth_pending SET used_at=now() WHERE request_hash=%s',(token_hash(body.request),))
            reply = {'state':params.state} if params.state else {}
            if not body.approved:
                reply['error'] = 'access_denied'
            else:
                code = secrets.token_urlsafe(32)
                conn.execute('INSERT INTO oauth_codes(code_hash,principal_id,client_id,redirect_uri,resource,challenge,expires_at,redirect_explicit) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)',
                    (token_hash(code),principal.id,row['client_id'],str(params.redirect_uri),params.resource,
                     params.code_challenge,datetime.now(UTC)+timedelta(minutes=5),params.redirect_uri_provided_explicitly))
                accounts.audit(conn,request.state.principal.id,request.state.principal.id,'oauth_authorized',client_id=row['client_id'])
                reply['code'] = code
            redirect = str(params.redirect_uri)
            return {'redirect':redirect+('&' if urlsplit(redirect).query else '?')+urlencode(reply)}

    return result

"""H5 login challenges and explicit binding of existing personal accounts."""
import secrets

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import Field

from . import accounts
from .auth import token_hash
from .dingtalk_identity import service
from .errors import KBError

CHALLENGE_COOKIE = 'agico_dingtalk_challenge'


class Login(accounts.Body):
    code: str = Field(min_length=1, max_length=1024, repr=False)
    challenge: str = Field(min_length=20, max_length=128, repr=False)


def require_service(db):
    current = service(db)
    if not current:
        raise KBError('DINGTALK_DISABLED', '当前实例未启用钉钉登录。', 404)
    return current


def router(db):
    routes = APIRouter()

    @routes.post('/v1/auth/dingtalk/challenge')
    def challenge(request: Request):
        require_service(db)
        accounts.same_origin(request)
        secret, browser = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        address = request.client.host if request.client else 'unknown'
        key = token_hash('ding-challenge:' + address)
        blocked = False
        with db.connection(write=True) as conn:
            counter = conn.execute("""INSERT INTO account_login_limits(key_hash,failures) VALUES (%s,1)
                ON CONFLICT(key_hash) DO UPDATE SET failures=CASE WHEN account_login_limits.window_start<now()-interval '15 minutes'
                  THEN 1 ELSE account_login_limits.failures+1 END,
                window_start=CASE WHEN account_login_limits.window_start<now()-interval '15 minutes' THEN now() ELSE account_login_limits.window_start END
                RETURNING failures""", (key,)).fetchone()
            blocked = counter['failures'] > 60
            conn.execute('DELETE FROM dingtalk_challenges WHERE expires_at<now()')
            if not blocked:
                conn.execute('INSERT INTO dingtalk_challenges(challenge_hash,browser_hash) VALUES (%s,%s)',
                             (token_hash(secret), token_hash(browser)))
        if blocked:
            raise KBError('RATE_LIMITED', '尝试次数过多，请稍后重试。', 429)
        response = JSONResponse({'challenge': secret})
        response.set_cookie(CHALLENGE_COOKIE, browser, max_age=180, httponly=True,
                            secure=request.url.scheme == 'https', samesite='strict', path='/v1/auth/dingtalk')
        return response

    def exchange(body, request, binding=None):
        current = require_service(db)
        accounts.same_origin(request)
        with db.connection(write=True) as conn:
            used = conn.execute("""UPDATE dingtalk_challenges SET used_at=now()
                WHERE challenge_hash=%s AND browser_hash=%s AND used_at IS NULL AND expires_at>now() RETURNING challenge_hash""",
                (token_hash(body.challenge), token_hash(request.cookies.get(CHALLENGE_COOKIE, '')))).fetchone()
        if not used:
            raise KBError('DINGTALK_CHALLENGE', '登录校验已失效，请重新进入应用。', 403)
        employee = current.client.identity(body.code)
        principal_id = current.login(employee, binding=binding,
                                     binding_session_hash=token_hash(request.cookies.get(accounts.COOKIE, '')) if binding else None)
        with db.connection(write=True) as conn:
            # A concurrent account disable or identity removal cannot mint a session.
            principal = accounts.load_principal(conn, principal_id, lock=True)
            if not principal:
                raise KBError('ACCOUNT_DISABLED', '账号不可用，请联系管理员。', 403)
            response = accounts.session_response(conn, principal_id, request, provider='dingtalk')
        response.delete_cookie(CHALLENGE_COOKIE, path='/v1/auth/dingtalk')
        return response

    @routes.post('/v1/auth/dingtalk/login')
    def login(body: Login, request: Request):
        return exchange(body, request)

    @routes.post('/v1/auth/dingtalk/bind')
    def bind(body: Login, request: Request):
        # Existing authenticated identity and CSRF are checked by middleware.
        with db.connection() as conn:
            accounts.require_current_session(conn, request)
        return exchange(body, request, binding=request.state.principal.id)

    return routes

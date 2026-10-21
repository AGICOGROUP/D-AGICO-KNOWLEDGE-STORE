"""Invite-only personal accounts, hierarchy and browser sessions."""
import hmac
import json
import re
import secrets
from contextvars import ContextVar
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from .account_security import hash_password, verify_password
from .auth import DEFAULT_VISIBLE_ORGANIZATIONS, Principal, token_hash
from .catalog import ordered_organizations
from .errors import KBError

COOKIE = 'agico_session'
_identity_service = ContextVar('agico_identity_service', default=None)
DUMMY_HASH = hash_password('This is not an account password')


class Body(BaseModel):
    model_config = ConfigDict(extra='forbid')


class Login(Body):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=1024, repr=False)


class Invitation(Body):
    invitation: str = Field(min_length=20, max_length=128, repr=False)


class Activate(Invitation):
    password: str = Field(min_length=8, max_length=128, repr=False)
    password_confirm: str = Field(min_length=8, max_length=128, repr=False)


class ChangePassword(Body):
    current_password: str = Field(min_length=1, max_length=1024, repr=False)
    password: str = Field(min_length=8, max_length=128, repr=False)
    password_confirm: str = Field(min_length=8, max_length=128, repr=False)


class InviteAccount(Body):
    email: str = Field(min_length=3, max_length=254)
    role: str
    organizations: list[str] = Field(default_factory=list, max_length=100)


class AccountUpdate(Body):
    role: str
    organizations: list[str] = Field(default_factory=list, max_length=100)
    active: bool = True


def normalize_email(email):
    value = email.strip().lower()
    if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', value):
        raise KBError('INVALID_EMAIL', '请填写有效的邮箱账号。', 422)
    return value


def enabled(db):
    if getattr(db, 'dingtalk', None):
        return True
    with db.connection() as conn:
        return conn.execute('SELECT enabled FROM account_settings WHERE id=1').fetchone()['enabled']


def audit(conn, actor, target, action, **detail):
    conn.execute('INSERT INTO account_audit(actor_id,target_id,action,detail) VALUES (%s,%s,%s,%s::jsonb)',
                 (actor, target, action, json.dumps(detail)))


def require_local_registration(db):
    if getattr(db, 'dingtalk', None):
        raise KBError('DINGTALK_MANAGED', '员工身份由钉钉管理，无需邀请或设置知识库密码。', 409)


def load_principal(conn, principal_id, *, lock=False, maintenance=False):
    row = conn.execute("""SELECT u.*,p.active FROM account_users u JOIN principals p ON p.id=u.principal_id
                          WHERE u.principal_id=%s""" + (' FOR UPDATE OF u' if lock else ''), (principal_id,)).fetchone()
    if not row or not row['active'] or row['status'] != 'active':
        return None
    if row['account_role'] == 'superadmin':
        memberships = {item['id']:'publisher' for item in conn.execute('SELECT id FROM organizations').fetchall()}
    else:
        memberships = {item['organization_id']:item['role'] for item in conn.execute(
            'SELECT organization_id,role FROM memberships WHERE principal_id=%s', (principal_id,)).fetchall()}
        for organization in DEFAULT_VISIBLE_ORGANIZATIONS:
            memberships.setdefault(organization,'member')
    bound = False
    from .dingtalk_identity import effective_identity
    current = _identity_service.get()
    maintenance = bool(maintenance and current and row['account_role'] == 'superadmin' and
                       principal_id in current.config.maintenance_principals)
    if current and not maintenance:
        result = effective_identity(conn, principal_id, row['account_role'], memberships, current)
        if result is None:
            return None
        memberships, bound = result
    if not row['password_hash'] and not bound:
        return None
    return Principal(principal_id, memberships, row['account_role'] == 'superadmin',
                     row['account_role'], bool(row['must_change_password'] and not bound), bound, maintenance)


def authenticate_session(db, secret):
    if not secret:
        return None
    from .dingtalk_identity import ensure_current
    with db.connection() as conn:
        owner = conn.execute('SELECT principal_id,auth_provider FROM account_sessions WHERE session_hash=%s AND expires_at>now()',
                             (token_hash(secret),)).fetchone()
        row = conn.execute('SELECT * FROM account_users WHERE principal_id=%s', (owner['principal_id'],)).fetchone() if owner else None
        from .dingtalk_identity import password_allowed
        maintenance = bool(owner and owner['auth_provider'] == 'local' and getattr(db, 'dingtalk', None) and password_allowed(db, row))
    if not owner or (not maintenance and not ensure_current(db, owner['principal_id'])):
        return None
    with db.connection() as conn:
        row = conn.execute('SELECT * FROM account_sessions WHERE session_hash=%s AND expires_at>now()',
                           (token_hash(secret),)).fetchone()
        principal = load_principal(conn, row['principal_id'], maintenance=maintenance) if row else None
        return (principal, row['csrf_token']) if principal else None


def same_origin(request):
    origin = request.headers.get('origin')
    if origin and origin != str(request.base_url).rstrip('/'):
        raise KBError('CSRF', '请在知识库网页内执行此操作。', 403)


def validate_csrf(request, csrf):
    same_origin(request)
    if not hmac.compare_digest(request.headers.get('x-csrf-token', ''), csrf):
        raise KBError('CSRF', '登录校验已失效，请刷新页面后重试。', 403)


def revoke_sessions(conn, principal_id):
    conn.execute('DELETE FROM account_sessions WHERE principal_id=%s', (principal_id,))
    conn.execute('UPDATE oauth_grants SET revoked=true WHERE principal_id=%s', (principal_id,))
    conn.execute('UPDATE oauth_codes SET used_at=now() WHERE principal_id=%s AND used_at IS NULL', (principal_id,))


def replace_memberships(conn, principal_id, role, organizations, *, previous_role=None):
    # Default visibility adds reading, while unchanged formal manager accounts
    # retain management that was explicitly granted before defaults existed.
    previous_publishers = set()
    if role == 'manager' and previous_role == 'manager':
        previous_publishers = {row['organization_id'] for row in conn.execute(
            "SELECT organization_id FROM memberships WHERE principal_id=%s AND role='publisher' AND organization_id=ANY(%s)",
            (principal_id,list(DEFAULT_VISIBLE_ORGANIZATIONS))).fetchall()}
    conn.execute('DELETE FROM memberships WHERE principal_id=%s', (principal_id,))
    for organization in organizations:
        conn.execute('INSERT INTO memberships VALUES (%s,%s,%s)',
                     (principal_id, organization, 'publisher' if role == 'superadmin' or
                      (role == 'manager' and (organization not in DEFAULT_VISIBLE_ORGANIZATIONS or organization in previous_publishers)) else 'member'))
    conn.execute('UPDATE principals SET is_admin=%s WHERE id=%s', (role == 'superadmin', principal_id))


def bootstrap_superadmin(db, email, password, *, must_change=True):
    email = normalize_email(email)
    digest = hash_password(password)
    with db.connection(write=True) as conn:
        conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(current_schema() || ':accounts-bootstrap',0))")
        if conn.execute('SELECT 1 FROM account_users LIMIT 1').fetchone():
            raise KBError('ALREADY_INITIALIZED', '已有账号，不能重复初始化或重置管理员。', 409)
        principal = 'account-' + uuid4().hex
        conn.execute('INSERT INTO principals(id,name,is_admin) VALUES (%s,%s,true)', (principal, email))
        conn.execute("""INSERT INTO account_users(principal_id,email,account_role,status,password_hash,must_change_password)
                        VALUES (%s,%s,'superadmin','active',%s,%s)""", (principal, email, digest, must_change))
        organizations = [row['id'] for row in conn.execute('SELECT id FROM organizations').fetchall()]
        replace_memberships(conn, principal, 'superadmin', organizations)
        conn.execute('UPDATE account_settings SET enabled=true WHERE id=1')
        audit(conn, principal, principal, 'bootstrap_superadmin')
        return principal


def session_response(conn, principal_id, request, *, provider='local'):
    secret, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    conn.execute('INSERT INTO account_sessions(session_hash,principal_id,csrf_token,auth_provider) VALUES (%s,%s,%s,%s)',
                 (token_hash(secret), principal_id, csrf, provider))
    response = JSONResponse(profile(conn, principal_id, csrf, provider=provider), headers={'Cache-Control':'no-store'})
    response.set_cookie(COOKIE, secret, max_age=8*3600, httponly=True, samesite='lax',
                        secure=request.url.scheme == 'https', path='/')
    return response


def profile(conn, principal_id, csrf, *, provider=None):
    row = conn.execute('SELECT email,account_role,must_change_password FROM account_users WHERE principal_id=%s',
                       (principal_id,)).fetchone()
    current = _identity_service.get()
    identity = conn.execute('SELECT display_name FROM dingtalk_identities WHERE principal_id=%s', (principal_id,)).fetchone() if current and provider != 'local' else None
    return {'email':row['email'], 'role':row['account_role'],
            'display_name':identity['display_name'] if identity else row['email'],
            'auth_provider':'dingtalk' if identity else 'local', 'auth_mode':'dingtalk' if current else 'local',
            'must_change_password':bool(row['must_change_password'] and not identity), 'csrf_token':csrf}


def actor_row(conn, principal):
    row = conn.execute("SELECT * FROM account_users WHERE principal_id=%s AND status='active'", (principal.id,)).fetchone()
    if not row or row['account_role'] not in {'superadmin','manager'}:
        raise KBError('FORBIDDEN', '当前账号没有账号管理或邀请权限。', 403)
    return row


def require_current_session(conn, request):
    principal = load_principal(conn,request.state.principal.id,lock=True, maintenance=request.state.principal.maintenance_session)
    session = conn.execute('SELECT 1 FROM account_sessions WHERE session_hash=%s AND principal_id=%s AND expires_at>now()',
        (token_hash(request.cookies.get(COOKIE,'')),request.state.principal.id)).fetchone()
    if not principal or principal.must_change_password or not session:
        raise KBError('UNAUTHENTICATED','登录已失效，请重新登录。',401)
    return principal


def management_actor(conn, request):
    # Serialize administration before taking actor/target locks. This also prevents
    # two superadministrators editing each other from acquiring reversed row locks.
    conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(current_schema() || ':account-administration',0))")
    principal = require_current_session(conn,request)
    actor_row(conn,principal)
    return principal


def scope_check(conn, principal, role, organizations, target=None):
    actor = actor_row(conn, principal)
    if role not in {'superadmin','manager','user'}:
        raise KBError('INVALID_ROLE', '请选择有效的账号角色。', 422)
    organizations = sorted(set(organizations) | set(DEFAULT_VISIBLE_ORGANIZATIONS))
    if actor['account_role'] != 'superadmin':
        managed = {key for key in principal.memberships if principal.publisher(key)}
        specific = set(organizations) - set(DEFAULT_VISIBLE_ORGANIZATIONS)
        explicitly_managed_defaults = managed & set(DEFAULT_VISIBLE_ORGANIZATIONS)
        if role not in {'manager', 'user'} or (not specific and not explicitly_managed_defaults) or not specific.issubset(managed):
            raise KBError('FORBIDDEN', '只能邀请或管理自己事业部的管理员和普通用户。', 403)
        if target:
            creator = conn.execute('SELECT created_by FROM account_users WHERE principal_id=%s',
                (target['principal_id'],)).fetchone()
            if not creator or creator['created_by'] != principal.id:
                raise KBError('FORBIDDEN', '只能管理自己登记的子用户。', 403)
            existing = conn.execute('SELECT organization_id FROM memberships WHERE principal_id=%s', (target['principal_id'],)).fetchall()
            specific_existing = {row['organization_id'] for row in existing} - set(DEFAULT_VISIBLE_ORGANIZATIONS)
            if target['account_role'] not in {'manager', 'user'} or (not specific_existing and not explicitly_managed_defaults) or not specific_existing.issubset(managed):
                raise KBError('FORBIDDEN', '无权管理这个账号。', 403)
    if role == 'superadmin':
        return [row['id'] for row in conn.execute('SELECT id FROM organizations').fetchall()]
    if not organizations or len(conn.execute('SELECT id FROM organizations WHERE id=ANY(%s)', (organizations,)).fetchall()) != len(organizations):
        raise KBError('INVALID_ORGANIZATION', '至少选择一个有效的事业部。', 422)
    return organizations


def issue_invitation(conn, principal_id, actor_id, purpose):
    conn.execute('UPDATE account_invitations SET revoked_at=now() WHERE principal_id=%s AND used_at IS NULL AND revoked_at IS NULL', (principal_id,))
    invitation = secrets.token_urlsafe(32)
    expires = datetime.now(UTC) + timedelta(days=7)
    conn.execute('INSERT INTO account_invitations(id,principal_id,secret_hash,purpose,created_by,expires_at) VALUES (%s,%s,%s,%s,%s,%s)',
                 (uuid4(), principal_id, token_hash(invitation), purpose, actor_id, expires))
    audit(conn, actor_id, principal_id, 'invitation_issued', purpose=purpose)
    return {'invitation':invitation, 'expires_at':expires.isoformat()}


def valid_invitation(conn, secret, *, lock=False):
    if lock:
        owner = conn.execute('SELECT principal_id FROM account_invitations WHERE secret_hash=%s',(token_hash(secret),)).fetchone()
        if owner:
            conn.execute('SELECT principal_id FROM account_users WHERE principal_id=%s FOR UPDATE',(owner['principal_id'],))
    row = conn.execute("""SELECT i.*,u.email,u.account_role,u.status FROM account_invitations i
        JOIN account_users u ON u.principal_id=i.principal_id
        WHERE i.secret_hash=%s AND i.used_at IS NULL AND i.revoked_at IS NULL AND i.expires_at>now()
        AND u.status!='disabled'""" + (' FOR UPDATE OF i' if lock else ''), (token_hash(secret),)).fetchone()
    if not row:
        raise KBError('INVITATION_EXPIRED', '邀请链接已失效、已使用或被撤销，请联系邀请你的管理员。', 410)
    issuer = load_principal(conn, row['created_by'])
    if not issuer:
        raise KBError('INVITATION_EXPIRED', '邀请人账号已停用，请联系管理员重新邀请。', 410)
    # Invitation remains valid only while its issuer still has authority over its recipient.
    orgs = [m['organization_id'] for m in conn.execute('SELECT organization_id FROM memberships WHERE principal_id=%s', (row['principal_id'],)).fetchall()]
    scope_check(conn, issuer, row['account_role'], orgs, target=row)
    return row


def account_tree(rows, actor_id, is_superadmin):
    """Paginate roots later, keeping each creator's complete branch together."""
    nodes = {row['principal_id']: {**row, 'children': [], 'parent_id': None,
        'can_manage': is_superadmin or row['principal_id'] != actor_id} for row in rows}
    roots = []
    for row in rows:
        node = nodes[row['principal_id']]
        parent = nodes.get(row['created_by'])
        # Superadministrators are peers regardless of which one invited them.
        if row['role'] != 'superadmin' and parent and parent is not node:
            node['parent_id'] = parent['principal_id']
            parent['children'].append(node)
        else:
            roots.append(node)
    return roots


def router(db):
    routes = APIRouter()

    @routes.get('/v1/auth/settings')
    def settings():
        from .dingtalk_identity import service
        current = service(db)
        if current:
            return {'enabled':enabled(db), 'registration':'dingtalk', 'auth_mode':'dingtalk',
                    'corp_id':current.config.corp_id, 'maintenance_login':bool(current.config.maintenance_principals)}
        return {'enabled':enabled(db), 'registration':'invitation_only'}

    @routes.post('/v1/auth/login')
    def password_login(body: Login, request: Request):
        same_origin(request)
        if not enabled(db):
            raise KBError('NOT_INITIALIZED', '账号系统尚未初始化，请联系管理员。', 503)
        email = normalize_email(body.email)
        address = request.client.host if request.client else 'unknown'
        keys = sorted([token_hash('login:'+email+':'+address), token_hash('address:'+address)])
        outcome = 'failed'
        with db.connection(write=True) as conn:
            for key in keys:
                conn.execute("""INSERT INTO account_login_limits(key_hash) VALUES (%s) ON CONFLICT(key_hash)
                    DO UPDATE SET failures=CASE WHEN account_login_limits.window_start<now()-interval '15 minutes' THEN 0 ELSE account_login_limits.failures END,
                    window_start=CASE WHEN account_login_limits.window_start<now()-interval '15 minutes' THEN now() ELSE account_login_limits.window_start END""", (key,))
            counters = conn.execute('SELECT * FROM account_login_limits WHERE key_hash=ANY(%s) ORDER BY key_hash FOR UPDATE', (keys,)).fetchall()
            blocked = any(row['failures'] >= (50 if row['key_hash'] == token_hash('address:'+address) else 10) for row in counters)
            if blocked:
                outcome = 'blocked'
            else:
                row = conn.execute('SELECT u.*,p.active FROM account_users u JOIN principals p ON p.id=u.principal_id WHERE u.email=%s FOR UPDATE OF u', (email,)).fetchone()
                correct = verify_password(body.password, row['password_hash'] if row and row['password_hash'] else DUMMY_HASH)
                from .dingtalk_identity import password_allowed
                correct = correct and password_allowed(db, row)
                if correct and row and row['active'] and row['status'] == 'active':
                    conn.execute('UPDATE account_login_limits SET failures=0 WHERE key_hash=%s', (token_hash('login:'+email+':'+address),))
                    audit(conn, row['principal_id'], row['principal_id'], 'login')
                    response = session_response(conn, row['principal_id'], request)
                    outcome = 'success'
                else:
                    conn.execute('UPDATE account_login_limits SET failures=failures+1 WHERE key_hash=ANY(%s)', (keys,))
        if outcome == 'blocked':
            raise KBError('RATE_LIMITED', '登录失败次数过多，请 15 分钟后再试。', 429)
        if outcome != 'success':
            raise KBError('INVALID_LOGIN', '邮箱或密码不正确，或账号尚未激活／已停用。', 401)
        return response

    @routes.get('/v1/auth/me')
    def me(request: Request):
        with db.connection() as conn:
            return profile(conn, request.state.principal.id, request.state.csrf_token,
                           provider='local' if request.state.principal.maintenance_session else None)

    @routes.post('/v1/auth/logout')
    def logout(request: Request):
        with db.connection(write=True) as conn:
            conn.execute('DELETE FROM account_sessions WHERE session_hash=%s', (token_hash(request.cookies.get(COOKIE, '')),))
        response = JSONResponse({'status':'logged_out'})
        response.delete_cookie(COOKIE, path='/')
        return response

    @routes.post('/v1/auth/invitation')
    def invitation_info(body: Invitation, request: Request):
        require_local_registration(db)
        same_origin(request)
        with db.connection() as conn:
            row = valid_invitation(conn, body.invitation)
            orgs = ordered_organizations(conn.execute('SELECT o.id,o.name FROM organizations o JOIN memberships m ON m.organization_id=o.id WHERE m.principal_id=%s', (row['principal_id'],)).fetchall())
            return {'email':row['email'], 'role':row['account_role'], 'organizations':orgs,
                    'purpose':row['purpose'], 'expires_at':row['expires_at']}

    @routes.post('/v1/auth/activate')
    def activate_account(body: Activate, request: Request):
        require_local_registration(db)
        same_origin(request)
        if body.password != body.password_confirm:
            raise KBError('PASSWORD_MISMATCH', '两次输入的密码不一致。', 422)
        digest = hash_password(body.password)
        with db.connection(write=True) as conn:
            row = valid_invitation(conn, body.invitation, lock=True)
            conn.execute('UPDATE account_invitations SET used_at=now() WHERE id=%s', (row['id'],))
            conn.execute("UPDATE account_users SET password_hash=%s,status='active',must_change_password=false,updated_at=now() WHERE principal_id=%s", (digest, row['principal_id']))
            conn.execute('UPDATE principals SET active=true WHERE id=%s', (row['principal_id'],))
            revoke_sessions(conn, row['principal_id'])
            audit(conn, row['principal_id'], row['principal_id'], 'password_set', purpose=row['purpose'])
            return session_response(conn, row['principal_id'], request)

    @routes.post('/v1/auth/change-password')
    def change_password(body: ChangePassword, request: Request):
        from .dingtalk_identity import password_allowed
        with db.connection() as conn:
            account = conn.execute('SELECT * FROM account_users WHERE principal_id=%s', (request.state.principal.id,)).fetchone()
        if not password_allowed(db, account):
            raise KBError('DINGTALK_MANAGED', '请通过钉钉管理员工登录。', 409)
        if body.password != body.password_confirm:
            raise KBError('PASSWORD_MISMATCH', '两次输入的密码不一致。', 422)
        with db.connection(write=True) as conn:
            row = conn.execute('SELECT * FROM account_users WHERE principal_id=%s FOR UPDATE', (request.state.principal.id,)).fetchone()
            if not verify_password(body.current_password, row['password_hash']):
                raise KBError('INVALID_PASSWORD', '当前密码不正确。', 422)
            if body.password == body.current_password:
                raise KBError('UNCHANGED_PASSWORD', '新密码不能与当前密码相同。', 422)
            conn.execute('UPDATE account_users SET password_hash=%s,must_change_password=false,updated_at=now() WHERE principal_id=%s', (hash_password(body.password), row['principal_id']))
            revoke_sessions(conn, row['principal_id'])
            audit(conn, row['principal_id'], row['principal_id'], 'password_changed')
            conn.execute('UPDATE account_invitations SET revoked_at=now() WHERE principal_id=%s AND used_at IS NULL', (row['principal_id'],))
            return session_response(conn, row['principal_id'], request)

    @routes.get('/v1/accounts')
    def list_accounts(request: Request, limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0),
                      tree: bool = False):
        with db.connection() as conn:
            actor = actor_row(conn, request.state.principal)
            parameters = [actor['account_role']=='superadmin', tree, actor['principal_id'], actor['principal_id'],
                [key for key in request.state.principal.memberships if request.state.principal.publisher(key)],
                list(request.state.principal.memberships)]
            pagination = '' if tree else ' LIMIT %s OFFSET %s'
            if not tree:
                parameters.extend([limit+1, offset])
            rows = conn.execute("""SELECT u.principal_id,u.email,u.account_role AS role,u.status,u.created_at,
                u.created_by,creator.email AS creator_email,di.display_name,di.organization_ids AS synchronized_organizations,
                COALESCE((SELECT json_agg(json_build_object('id',o.id,'name',o.name)) FROM memberships m
                JOIN organizations o ON o.id=m.organization_id WHERE m.principal_id=u.principal_id),'[]') AS organizations
                FROM account_users u LEFT JOIN account_users creator ON creator.principal_id=u.created_by
                LEFT JOIN dingtalk_identities di ON di.principal_id=u.principal_id
                WHERE %s OR (%s AND u.principal_id=%s) OR (u.account_role IN ('manager','user') AND u.created_by=%s
                AND EXISTS(SELECT 1 FROM memberships m WHERE m.principal_id=u.principal_id AND m.organization_id=ANY(%s))
                AND NOT EXISTS(SELECT 1 FROM memberships m WHERE m.principal_id=u.principal_id AND NOT (m.organization_id=ANY(%s))))
                ORDER BY CASE u.account_role WHEN 'superadmin' THEN 0 WHEN 'manager' THEN 1 ELSE 2 END,
                u.created_at,u.principal_id""" + pagination, parameters).fetchall()
            defaults = conn.execute('SELECT id,name FROM organizations WHERE id=ANY(%s)',(list(DEFAULT_VISIBLE_ORGANIZATIONS),)).fetchall()
            for row in rows:
                if getattr(db, 'dingtalk', None) and row['synchronized_organizations'] is not None and row['role'] != 'superadmin':
                    row['organizations'] = conn.execute('SELECT id,name FROM organizations WHERE id=ANY(%s)',
                                                       (row['synchronized_organizations'],)).fetchall()
                present = {org['id'] for org in row['organizations']}
                row['organizations'].extend(org for org in defaults if org['id'] not in present)
                row['organizations'] = ordered_organizations(row['organizations'])
            if tree:
                roots = account_tree(rows, actor['principal_id'], actor['account_role']=='superadmin')
                return {'items': roots[offset:offset+limit], 'has_more': len(roots)>offset+limit,
                        'total_accounts': len(rows), 'total_roots': len(roots)}
            return {'items':rows[:limit], 'has_more':len(rows)>limit}

    @routes.get('/v1/accounts/entrypoint')
    def entrypoint(request: Request):
        with db.connection() as conn:
            actor_row(conn,request.state.principal)
        return {'url':db.settings.public_url or str(request.base_url).rstrip('/')}

    @routes.post('/v1/accounts/invitations', status_code=201)
    def invite(body: InviteAccount, request: Request):
        require_local_registration(db)
        email = normalize_email(body.email)
        with db.connection(write=True) as conn:
            principal = management_actor(conn,request)
            organizations = scope_check(conn, principal, body.role, body.organizations)
            conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(current_schema() || ':invite:' || %s,0))", (email,))
            if conn.execute('SELECT 1 FROM account_users WHERE email=%s', (email,)).fetchone():
                raise KBError('ACCOUNT_EXISTS', '此邮箱已登记，请在账号列表重发邀请或管理账号。', 409)
            principal = 'account-' + uuid4().hex
            conn.execute('INSERT INTO principals(id,name,active) VALUES (%s,%s,false)', (principal,email))
            conn.execute("INSERT INTO account_users(principal_id,email,account_role,status,created_by) VALUES (%s,%s,%s,'invited',%s)",
                         (principal,email,body.role,request.state.principal.id))
            replace_memberships(conn,principal,body.role,organizations)
            return {'email':email, 'principal_id':principal, **issue_invitation(conn,principal,request.state.principal.id,'activate')}

    @routes.post('/v1/accounts/{principal_id}/invitation')
    def resend(principal_id: str, request: Request):
        require_local_registration(db)
        with db.connection(write=True) as conn:
            principal = management_actor(conn,request)
            target = conn.execute('SELECT * FROM account_users WHERE principal_id=%s FOR UPDATE', (principal_id,)).fetchone()
            if not target:
                raise KBError('NOT_FOUND', '账号不存在。', 404)
            orgs = [row['organization_id'] for row in conn.execute('SELECT organization_id FROM memberships WHERE principal_id=%s', (principal_id,)).fetchall()]
            scope_check(conn,principal,target['account_role'],orgs,target)
            if target['status'] == 'disabled':
                raise KBError('ACCOUNT_DISABLED', '请先启用账号，再生成链接。', 409)
            return {'email':target['email'], **issue_invitation(conn,principal_id,request.state.principal.id,
                        'activate' if target['status']=='invited' else 'reset')}

    @routes.patch('/v1/accounts/{principal_id}')
    def update(principal_id: str, body: AccountUpdate, request: Request):
        with db.connection(write=True) as conn:
            principal = management_actor(conn,request)
            target = conn.execute('SELECT * FROM account_users WHERE principal_id=%s FOR UPDATE', (principal_id,)).fetchone()
            if not target:
                raise KBError('NOT_FOUND', '账号不存在。', 404)
            organizations = scope_check(conn,principal,body.role,body.organizations,target)
            if target['account_role']=='superadmin' and target['status']=='active' and (body.role!='superadmin' or not body.active):
                count = conn.execute("SELECT count(*) AS n FROM account_users WHERE account_role='superadmin' AND status='active'").fetchone()['n']
                if count <= 1:
                    raise KBError('LAST_SUPERADMIN', '不能停用或降级最后一个开发人员。', 409)
            linked = conn.execute('SELECT 1 FROM dingtalk_identities WHERE principal_id=%s', (principal_id,)).fetchone()
            status = ('active' if target['password_hash'] or linked else 'invited') if body.active else 'disabled'
            conn.execute('UPDATE account_users SET account_role=%s,status=%s,updated_at=now() WHERE principal_id=%s', (body.role,status,principal_id))
            conn.execute('UPDATE principals SET active=%s WHERE id=%s', (status=='active',principal_id))
            replace_memberships(conn,principal_id,body.role,organizations,previous_role=target['account_role'])
            revoke_sessions(conn,principal_id)
            if not body.active:
                conn.execute('UPDATE account_invitations SET revoked_at=now() WHERE principal_id=%s AND used_at IS NULL', (principal_id,))
            audit(conn,request.state.principal.id,principal_id,'account_updated',role=body.role,status=status,organizations=organizations)
            return {'status':status}

    return routes


def main():
    import argparse
    import getpass

    from .config import Settings
    from .db import Database
    parser = argparse.ArgumentParser(description='Initialize the first personal superadministrator once')
    parser.add_argument('action', choices=['bootstrap'])
    parser.add_argument('--email', required=True)
    parser.add_argument('--password-stdin', action='store_true')
    args = parser.parse_args()
    if args.password_stdin:
        import sys
        password = sys.stdin.readline().rstrip('\r\n')
    else:
        password = getpass.getpass('Initial password: ')
    db = Database(Settings.from_env())
    try:
        db.migrate()
        bootstrap_superadmin(db,args.email,password)
        print('First superadministrator created; password change required on first login.')
    finally:
        db.close()


if __name__ == '__main__':
    main()

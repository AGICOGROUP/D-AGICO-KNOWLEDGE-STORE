"""Additive corporate identity bindings; no company directory writes."""
import hashlib
import json
import threading
import time
from datetime import datetime, timezone
from uuid import uuid4

from . import accounts
from .auth import DEFAULT_VISIBLE_ORGANIZATIONS
from .errors import KBError


def service(db):
    return getattr(db, 'dingtalk', None)


def password_allowed(db, row):
    current = service(db)
    return not current or bool(row and row['account_role'] == 'superadmin' and
                               row['principal_id'] in current.config.maintenance_principals)


def ensure_current(db, principal_id):
    current = service(db)
    return not current or current.ensure_fresh(principal_id)


class IdentityService:
    def __init__(self, db, client):
        self.db, self.client, self.config = db, client, client.config
        self._locks = [threading.Lock() for _ in range(32)]
        self._directory_lock = threading.Lock()
        self._hierarchy, self._hierarchy_until = [], 0
        self._hierarchy_at = None

    def lock(self, user_id):
        slot = int(hashlib.sha256(user_id.encode()).hexdigest()[:4], 16) % len(self._locks)
        return self._locks[slot]

    def hierarchy(self):
        with self._directory_lock:
            if self._hierarchy_until <= time.monotonic():
                # Assign only a complete, validated hierarchy; failures preserve no fresh TTL.
                started = datetime.now(timezone.utc)
                result = self.client.departments()
                self._hierarchy, self._hierarchy_until = result, time.monotonic() + self.config.refresh_seconds
                self._hierarchy_at = started
            return self._hierarchy, self._hierarchy_at

    def save(self, conn, principal_id, employee, organizations, verified_at):
        conn.execute("""INSERT INTO dingtalk_identities
            (corp_id,user_id,principal_id,display_name,union_id,department_ids,organization_ids,enabled,verified_at)
            VALUES (%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s)
            ON CONFLICT(corp_id,user_id) DO UPDATE SET display_name=excluded.display_name,
              union_id=excluded.union_id,department_ids=excluded.department_ids,
              organization_ids=excluded.organization_ids,enabled=excluded.enabled,verified_at=excluded.verified_at""",
            (self.config.corp_id, employee.user_id, principal_id, employee.name, employee.union_id,
             json.dumps(employee.department_ids), json.dumps(sorted(organizations)), employee.active, verified_at))

    def login(self, employee, *, binding=None, binding_session_hash=None):
        if not employee.active:
            raise KBError('DINGTALK_INACTIVE', '当前钉钉账号尚未激活或不可用。', 403)
        started = datetime.now(timezone.utc)
        hierarchy, hierarchy_at = self.hierarchy()
        if not self.client.visible(employee, hierarchy):
            raise KBError('DINGTALK_APP_DENIED', '当前员工不在应用可见范围内。', 403)
        organizations = self.client.organizations(employee.department_ids, hierarchy)
        with self.db.connection(write=True) as conn:
            # Same account-administration lock ordering as existing role updates.
            conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(current_schema() || ':account-administration',0))")
            conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(current_schema() || ':ding:' || %s,0))",
                         (self.config.corp_id + ':' + employee.user_id,))
            identity = conn.execute('SELECT * FROM dingtalk_identities WHERE corp_id=%s AND user_id=%s FOR UPDATE',
                                    (self.config.corp_id, employee.user_id)).fetchone()
            if binding:
                principal_id = binding
                if identity and identity['principal_id'] != binding:
                    raise KBError('DINGTALK_ALREADY_BOUND', '该钉钉身份已关联其他账号，请联系开发人员核对。', 409)
                other = conn.execute('SELECT * FROM dingtalk_identities WHERE principal_id=%s', (binding,)).fetchone()
                if other and (other['corp_id'], other['user_id']) != (self.config.corp_id, employee.user_id):
                    raise KBError('DINGTALK_ALREADY_BOUND', '当前账号已关联其他钉钉身份。', 409)
            elif identity:
                principal_id = identity['principal_id']
            else:
                principal_id = 'account-' + uuid4().hex
                email = 'ding-' + hashlib.sha256((self.config.corp_id + ':' + employee.user_id).encode()).hexdigest() + '@dingtalk.invalid'
                conn.execute('INSERT INTO principals(id,name) VALUES (%s,%s)', (principal_id, employee.name))
                conn.execute("""INSERT INTO account_users(principal_id,email,account_role,status)
                    VALUES (%s,%s,'user','active')""", (principal_id, email))
            user = conn.execute('SELECT u.*,p.active FROM account_users u JOIN principals p ON p.id=u.principal_id WHERE u.principal_id=%s FOR UPDATE OF u',
                                (principal_id,)).fetchone()
            if not user or not user['active'] or user['status'] != 'active':
                raise KBError('ACCOUNT_DISABLED', '知识库账号已停用，请联系管理员。', 403)
            if binding and not conn.execute('''SELECT 1 FROM account_sessions
                    WHERE session_hash=%s AND principal_id=%s AND expires_at>now()''',
                    (binding_session_hash, binding)).fetchone():
                raise KBError('UNAUTHENTICATED', '登录已失效，请重新登录后关联钉钉。', 401)
            self.save(conn, principal_id, employee, organizations, min(started, hierarchy_at))
            accounts.audit(conn, principal_id, principal_id, 'dingtalk_bound' if binding else 'dingtalk_login')
            # Preserve password hash and first-login flag; linked profiles bypass local-password UX.
            return principal_id

    def ensure_fresh(self, principal_id):
        with self.db.connection() as conn:
            row = conn.execute("SELECT *,verified_at>now()-(%s * interval '1 second') AS fresh FROM dingtalk_identities WHERE principal_id=%s",
                               (self.config.refresh_seconds, principal_id)).fetchone()
        if not row:
            return principal_id in self.config.maintenance_principals
        if row['corp_id'] != self.config.corp_id:
            return False
        if row['fresh']:
            return row['enabled']
        with self.lock(row['user_id']):
            with self.db.connection() as conn:
                latest = conn.execute("SELECT *,verified_at>now()-(%s * interval '1 second') AS fresh FROM dingtalk_identities WHERE principal_id=%s",
                                      (self.config.refresh_seconds, principal_id)).fetchone()
            if not latest:
                return False
            if latest['fresh']:
                return latest['enabled']
            try:
                started = datetime.now(timezone.utc)
                employee = self.client.employee(row['user_id'])
                if employee.user_id != row['user_id']:
                    return False
                hierarchy, hierarchy_at = self.hierarchy()
                if not self.client.visible(employee, hierarchy):
                    raise KBError('DINGTALK_APP_DENIED', '当前员工不在应用可见范围内。', 403)
                organizations = self.client.organizations(employee.department_ids, hierarchy)
            except KBError as exc:
                if exc.payload['error']['code'] in {'DINGTALK_IDENTITY_REMOVED', 'DINGTALK_APP_DENIED'}:
                    with self.db.connection(write=True) as conn:
                        unchanged, usable = self.refresh_write_guard(conn, principal_id, latest['verified_at'])
                        if not unchanged:
                            return usable
                        conn.execute('UPDATE dingtalk_identities SET enabled=false,verified_at=now() WHERE principal_id=%s', (principal_id,))
                        accounts.revoke_sessions(conn, principal_id)
                        accounts.audit(conn, principal_id, principal_id, 'dingtalk_access_revoked')
                return False
            with self.db.connection(write=True) as conn:
                unchanged, usable = self.refresh_write_guard(conn, principal_id, latest['verified_at'])
                if not unchanged:
                    return usable
                self.save(conn, principal_id, employee, organizations, min(started, hierarchy_at))
                if not employee.active:
                    accounts.revoke_sessions(conn, principal_id)
                    accounts.audit(conn, principal_id, principal_id, 'dingtalk_access_revoked')
            return employee.active

    def refresh_write_guard(self, conn, principal_id, expected):
        # Network I/O is finished. Serialize with login/admin writes and discard an
        # obsolete response if another API process has already replaced its evidence.
        conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(current_schema() || ':account-administration',0))")
        conn.execute('SELECT principal_id FROM account_users WHERE principal_id=%s FOR UPDATE', (principal_id,))
        row = conn.execute("""SELECT verified_at,enabled,
            verified_at>now()-(%s * interval '1 second') AS fresh
            FROM dingtalk_identities WHERE principal_id=%s""", (self.config.refresh_seconds, principal_id)).fetchone()
        return bool(row and row['verified_at'] == expected), bool(row and row['enabled'] and row['fresh'])

    def synchronize(self):
        with self.db.connection() as conn:
            rows = conn.execute("SELECT principal_id FROM dingtalk_identities WHERE corp_id=%s AND verified_at<now()-(%s * interval '1 second') ORDER BY verified_at LIMIT 100",
                                (self.config.corp_id, self.config.refresh_seconds)).fetchall()
        for row in rows:
            self.ensure_fresh(row['principal_id'])


def effective_identity(conn, principal_id, account_role, memberships, current):
    row = conn.execute("SELECT *,verified_at>now()-(%s * interval '1 second') AS fresh FROM dingtalk_identities WHERE principal_id=%s",
                       (current.config.refresh_seconds, principal_id)).fetchone()
    if not row:
        return (memberships, False) if account_role == 'superadmin' and principal_id in current.config.maintenance_principals else None
    if row['corp_id'] != current.config.corp_id or not row['enabled'] or not row['fresh']:
        return None
    if account_role == 'superadmin':
        return memberships, True
    result = {organization: 'member' for organization in set(row['organization_ids']) | set(DEFAULT_VISIBLE_ORGANIZATIONS)}
    if account_role == 'manager':
        for organization in result:
            if memberships.get(organization) == 'publisher':
                result[organization] = 'publisher'
    # Explicit additions grant reading only. Never turn an out-of-department
    # legacy publisher membership into publishing authority through an extension.
    for grant in conn.execute('SELECT organization_id FROM account_access_extensions WHERE principal_id=%s',
                              (principal_id,)).fetchall():
        result.setdefault(grant['organization_id'], 'member')
    return result, True

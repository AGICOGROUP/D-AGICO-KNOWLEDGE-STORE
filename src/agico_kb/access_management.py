"""Administrator-only member credential issuance; secrets are returned once, never stored."""

import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import APIRouter, Query, Request
from psycopg.errors import UniqueViolation
from pydantic import Field

from .auth import token_hash
from .contracts import Contract
from .errors import KBError


class NewAccess(Contract):
    request_id: UUID
    name: str = Field(min_length=1, max_length=100)
    organizations: list[str] = Field(min_length=1, max_length=100)
    days: int = Field(default=30, ge=1, le=365)


def require_admin(conn, principal):
    row = conn.execute(
        "SELECT is_admin,active FROM principals WHERE id=%s FOR SHARE", (principal.id,)
    ).fetchone()
    if not row or not row["active"] or not row["is_admin"]:
        raise KBError("FORBIDDEN", "需要管理员访问码，普通成员或审核人员不能管理访问码。", 403)


def router(db):
    routes = APIRouter(prefix="/v1/admin")

    @routes.get("/session")
    def session(request: Request):
        with db.connection() as conn:
            require_admin(conn, request.state.principal)
            return {
                "identity": request.state.principal.id,
                "organizations": conn.execute(
                    "SELECT id,name FROM organizations ORDER BY name"
                ).fetchall(),
            }

    @routes.get("/access")
    def listing(
        request: Request, limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0)
    ):
        with db.connection() as conn:
            require_admin(conn, request.state.principal)
            rows = conn.execute(
                """SELECT a.id,a.principal_id,p.name,a.created_at,a.created_by,t.expires_at,
                a.revoked_at,a.revoked_by,
                CASE WHEN t.revoked OR NOT p.active THEN 'revoked'
                     WHEN t.expires_at <= now() THEN 'expired' ELSE 'active' END AS status,
                (SELECT json_agg(json_build_object('id',o.id,'name',o.name) ORDER BY o.name)
                 FROM memberships m JOIN organizations o ON o.id=m.organization_id
                 WHERE m.principal_id=a.principal_id) AS organizations
                FROM managed_access a JOIN principals p ON p.id=a.principal_id
                JOIN access_tokens t ON t.token_hash=a.token_hash
                ORDER BY a.created_at DESC,a.id DESC LIMIT %s OFFSET %s""",
                (limit + 1, offset),
            ).fetchall()
            return {"items": rows[:limit], "has_more": len(rows) > limit}

    @routes.post("/access", status_code=201)
    def create(body: NewAccess, request: Request):
        # A fresh identity per credential makes revocation and department boundaries unambiguous.
        principal_id = "shared-" + body.request_id.hex
        try:
            with db.connection(write=True) as conn:
                require_admin(conn, request.state.principal)
                organizations = sorted(set(body.organizations))
                found = conn.execute(
                    "SELECT id FROM organizations WHERE id=ANY(%s)", (organizations,)
                ).fetchall()
                if len(found) != len(organizations):
                    raise KBError("INVALID_ORGANIZATION", "请选择有效的事业部。", 422)
                if conn.execute(
                    "SELECT 1 FROM managed_access WHERE id=%s", (body.request_id,)
                ).fetchone():
                    raise KBError(
                        "ALREADY_CREATED",
                        "这次生成已完成。原访问码只显示一次；如未保存，请在列表停用后重新生成。",
                        409,
                    )
                token = secrets.token_urlsafe(32)
                digest = token_hash(token)
                expires = datetime.now(UTC) + timedelta(days=body.days)
                conn.execute(
                    "INSERT INTO principals(id,name) VALUES (%s,%s)", (principal_id, body.name)
                )
                for org in organizations:
                    conn.execute(
                        "INSERT INTO memberships VALUES (%s,%s,'member')", (principal_id, org)
                    )
                conn.execute(
                    "INSERT INTO access_tokens(token_hash,principal_id,expires_at) VALUES (%s,%s,%s)",
                    (digest, principal_id, expires),
                )
                conn.execute(
                    "INSERT INTO managed_access(id,principal_id,token_hash,created_by) VALUES (%s,%s,%s,%s)",
                    (body.request_id, principal_id, digest, request.state.principal.id),
                )
                return {
                    "id": str(body.request_id),
                    "principal_id": principal_id,
                    "token": token,
                    "expires_at": expires,
                }
        except UniqueViolation:
            raise KBError(
                "ALREADY_CREATED", "这次生成已完成，请刷新列表确认；不要重复生成。", 409
            ) from None

    @routes.post("/access/{access_id}/revoke")
    def revoke(access_id: UUID, request: Request):
        with db.connection(write=True) as conn:
            require_admin(conn, request.state.principal)
            row = conn.execute(
                "SELECT principal_id FROM managed_access WHERE id=%s FOR UPDATE", (access_id,)
            ).fetchone()
            if not row:
                raise KBError("NOT_FOUND", "未找到这个受管理的访问码。", 404)
            conn.execute(
                "UPDATE access_tokens SET revoked=true WHERE principal_id=%s",
                (row["principal_id"],),
            )
            conn.execute(
                "UPDATE managed_access SET revoked_by=COALESCE(revoked_by,%s),revoked_at=COALESCE(revoked_at,now()) WHERE id=%s",
                (request.state.principal.id, access_id),
            )
            return {"status": "revoked"}

    return routes

import hashlib
from dataclasses import dataclass

DEFAULT_VISIBLE_ORGANIZATIONS = ('general-group', 'company-common')


@dataclass(frozen=True)
class Principal:
    id: str
    memberships: dict[str, str]
    is_admin: bool = False
    account_role: str | None = None
    must_change_password: bool = False

    def publisher(self, organization_id: str) -> bool:
        return self.memberships.get(organization_id) == "publisher"

    @property
    def department_restricted(self) -> bool:
        return self.account_role == "manager"

    def can_access_department(self, organization_id: str) -> bool:
        # A manager's assigned departments are a hard boundary, including old
        # uploads and individually shared files after assignments change.
        return not self.department_restricted or organization_id in self.memberships


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def authenticate(db, token: str, resource: str | None = None) -> Principal | None:
    with db.connection() as conn:
        if conn.execute("SELECT enabled FROM account_settings WHERE id=1").fetchone()['enabled']:
            from .accounts import load_principal
            row = conn.execute(
                """SELECT g.principal_id FROM oauth_credentials c JOIN oauth_grants g ON g.id=c.grant_id
                WHERE c.secret_hash=%s AND c.kind='access' AND c.expires_at>now()
                AND NOT g.revoked AND g.resource=%s""", (token_hash(token), resource),
            ).fetchone()
            principal = load_principal(conn, row['principal_id']) if row else None
            return principal if principal and not principal.must_change_password else None
        row = conn.execute(
            """SELECT p.id,p.is_admin FROM access_tokens t JOIN principals p ON p.id=t.principal_id
            WHERE t.token_hash=%s AND NOT t.revoked AND p.active
            AND (t.expires_at IS NULL OR t.expires_at > now())""",
            (token_hash(token),),
        ).fetchone()
        if row is None:
            return None
        memberships = conn.execute(
            "SELECT organization_id,role FROM memberships WHERE principal_id=%s", (row["id"],)
        ).fetchall()
        return Principal(
            row["id"], {m["organization_id"]: m["role"] for m in memberships}, row["is_admin"]
        )

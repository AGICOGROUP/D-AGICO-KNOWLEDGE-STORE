import hashlib
from dataclasses import dataclass


@dataclass(frozen=True)
class Principal:
    id: str
    memberships: dict[str, str]

    def publisher(self, organization_id: str) -> bool:
        return self.memberships.get(organization_id) == "publisher"


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def authenticate(db, token: str) -> Principal | None:
    with db.connection() as conn:
        row = conn.execute(
            """SELECT p.id FROM access_tokens t JOIN principals p ON p.id=t.principal_id
            WHERE t.token_hash=%s AND NOT t.revoked AND p.active
            AND (t.expires_at IS NULL OR t.expires_at > now())""",
            (token_hash(token),),
        ).fetchone()
        if row is None:
            return None
        memberships = conn.execute(
            "SELECT organization_id,role FROM memberships WHERE principal_id=%s", (row["id"],)
        ).fetchall()
        return Principal(row["id"], {m["organization_id"]: m["role"] for m in memberships})

"""Local operator commands; connection credentials come only from environment."""

import argparse
import secrets
from datetime import UTC, datetime, timedelta

from .auth import token_hash
from .config import Settings
from .db import Database


def set_identity(db, subject, name, organization, role):
    if not subject.strip() or role not in {"member", "publisher"}:
        raise ValueError("需要有效身份和角色")
    with db.connection(write=True) as conn:
        if not conn.execute("SELECT 1 FROM organizations WHERE id=%s", (organization,)).fetchone():
            raise ValueError("事业部不存在")
        conn.execute(
            """INSERT INTO principals(id,name) VALUES (%s,%s)
            ON CONFLICT(id) DO UPDATE SET name=excluded.name""",
            (subject, name),
        )
        conn.execute(
            """INSERT INTO memberships VALUES (%s,%s,%s)
            ON CONFLICT(principal_id,organization_id) DO UPDATE SET role=excluded.role""",
            (subject, organization, role),
        )


def issue_token(db, subject, expires_at):
    token = secrets.token_urlsafe(32)
    with db.connection(write=True) as conn:
        if not conn.execute(
            "SELECT 1 FROM principals WHERE id=%s AND active=true", (subject,)
        ).fetchone():
            raise ValueError("身份不存在或已停用")
        conn.execute(
            "INSERT INTO access_tokens(token_hash,principal_id,expires_at) VALUES (%s,%s,%s)",
            (token_hash(token), subject, expires_at),
        )
    return token


def revoke_tokens(db, subject):
    with db.connection(write=True) as conn:
        conn.execute("UPDATE access_tokens SET revoked=true WHERE principal_id=%s", (subject,))


def main():
    parser = argparse.ArgumentParser(description="知识库运维：迁移、身份配置、令牌签发与撤销")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("migrate")
    identity = commands.add_parser("set-identity")
    identity.add_argument("subject")
    identity.add_argument("name")
    identity.add_argument("organization")
    identity.add_argument("role", choices=["member", "publisher"])
    issue = commands.add_parser("issue-token")
    issue.add_argument("subject")
    issue.add_argument("--days", type=int, default=30)
    revoke = commands.add_parser("revoke-tokens")
    revoke.add_argument("subject")
    args = parser.parse_args()
    if args.command == "issue-token" and not 1 <= args.days <= 365:
        parser.error("--days 必须为 1 至 365")
    db = Database(Settings.from_env())
    try:
        if args.command == "migrate":
            db.migrate()
        elif args.command == "set-identity":
            set_identity(db, args.subject, args.name, args.organization, args.role)
        elif args.command == "issue-token":
            # Only this explicit operator action prints the secret, once. Do not log stdout.
            print(issue_token(db, args.subject, datetime.now(UTC) + timedelta(days=args.days)))
        elif args.command == "revoke-tokens":
            revoke_tokens(db, args.subject)
    finally:
        db.close()


if __name__ == "__main__":
    main()

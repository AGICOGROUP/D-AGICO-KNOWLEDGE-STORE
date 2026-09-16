from datetime import UTC, datetime, timedelta

from conftest import headers

from agico_kb import admin


def test_ops_identity_token_lifecycle(kb):
    client, db, _ = kb
    admin.set_identity(db, "new-user", "新员工", "baiste", "member")
    token = admin.issue_token(db, "new-user", datetime.now(UTC) + timedelta(hours=1))
    assert (
        client.get("/v1/catalog", headers={"Authorization": "Bearer " + token}).status_code == 200
    )
    admin.revoke_tokens(db, "new-user")
    assert (
        client.get("/v1/catalog", headers={"Authorization": "Bearer " + token}).status_code == 401
    )
    expired = admin.issue_token(db, "new-user", datetime.now(UTC) - timedelta(seconds=1))
    assert (
        client.get("/v1/catalog", headers={"Authorization": "Bearer " + expired}).status_code == 401
    )


def test_disabled_identity_cannot_read(kb):
    client, db, _ = kb
    with db.connection() as conn:
        conn.execute("UPDATE principals SET active=false WHERE id='alice'")
    assert client.get("/v1/catalog", headers=headers()).status_code == 401

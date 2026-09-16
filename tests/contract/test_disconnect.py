import asyncio
from uuid import UUID, uuid4

import pytest
from conftest import headers
from starlette.requests import ClientDisconnect

from agico_kb.auth import authenticate
from agico_kb.files import upload_content


def test_disconnect_after_partial_bytes_cleans_temp_and_cannot_submit(kb):
    client, db, root = kb
    response = client.post(
        "/v1/uploads", headers=headers(key=uuid4().hex), json={"filename": "断传.txt", "size": 100}
    )
    uid = response.json()["upload_id"]

    class DisconnectedStream:
        async def stream(self):
            yield b"partial fresh data"
            raise ClientDisconnect()

    # Inject a transport disconnect after bytes reached the actual disk writer.
    with pytest.raises(ClientDisconnect):
        asyncio.run(
            upload_content(
                db,
                client.app.state.settings,
                authenticate(db, "alice"),
                UUID(uid),
                DisconnectedStream(),
            )
        )
    assert list(root.iterdir()) == []
    with db.connection() as conn:
        row = conn.execute("SELECT state,blob_key FROM uploads WHERE id=%s", (uid,)).fetchone()
        assert row == {"state": "pending", "blob_key": None}
    assert (
        client.post(
            "/v1/submissions",
            headers=headers(),
            json={"upload_id": uid, "organization_id": "baiste", "title": "断传"},
        ).status_code
        == 409
    )

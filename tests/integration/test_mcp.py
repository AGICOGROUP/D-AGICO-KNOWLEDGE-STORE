import asyncio
import json
import socket
import time
from functools import partial
from uuid import uuid4

import httpx
import httpx2
import pytest
import uvicorn
from conftest import headers, submit
from mcp import Client
from mcp.client.streamable_http import streamable_http_client


@pytest.fixture
def live_server(kb_large):
    client, _db, _root = kb_large
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(128)
    sock.setblocking(False)
    port = sock.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(client.app, host="127.0.0.1", port=port, log_level="error", lifespan="off")
    )
    future = client.portal.start_task_soon(partial(server.serve, sockets=[sock]))
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.01)
    assert server.started
    try:
        yield f"http://127.0.0.1:{port}", kb_large
    finally:
        server.should_exit = True
        future.result(timeout=10)
        sock.close()


def decode(result):
    if result.structured_content is not None:
        return result.structured_content
    return json.loads(result.content[0].text)


def test_mcp_publisher_reviews_other_category_then_context_finds_background(
    live_server, real_embedder
):
    from agico_kb.worker import Worker

    base, kb = live_server
    client, db, _ = kb
    made, _, content = submit(client, category_id="other")
    vid = made.json()["version_id"]
    assert Worker(db, client.app.state.settings, embedder=real_embedder).run_once()

    async def scenario():
        async with (
            httpx2.AsyncClient(headers=headers("chief")) as http,
            Client(streamable_http_client(base + "/mcp", http_client=http), mode="legacy") as agent,
        ):
            assert (
                decode(await agent.call_tool("kb_context", {"organization_id": "baiste"}))["items"]
                == []
            )
            result = await agent.call_tool(
                "kb_publish",
                {"version_id": vid, "expected_revision": 0, "category_id": "background"},
            )
            assert not result.is_error, result
            found = decode(await agent.call_tool("kb_context", {"organization_id": "baiste"}))
            assert found["items"][0]["version_id"] == vid
            assert content.decode() in found["items"][0]["excerpt"]

    asyncio.run(scenario())


def test_real_http_mcp_lists_scoped_tools_and_uses_shared_upload_backend(live_server):
    base, kb = live_server
    assert httpx.post(base + "/mcp", json={}).status_code == 401

    async def scenario():
        async with (
            httpx2.AsyncClient(headers=headers()) as http,
            Client(streamable_http_client(base + "/mcp", http_client=http), mode="legacy") as agent,
        ):
            names = {t.name for t in (await agent.list_tools()).tools}
            assert {
                "kb_search",
                "kb_read",
                "kb_prepare_upload",
                "kb_submit",
                "kb_get_file",
            } <= names
            assert "kb_publish" not in names
            payload = "全新 MCP 上传资料：AX-210 电压为 220 V。".encode()
            prepared = await agent.call_tool(
                "kb_prepare_upload",
                {"filename": "资料.md", "size": len(payload), "idempotency_key": uuid4().hex},
            )
            uid = decode(prepared)["upload_id"]
            with httpx.Client() as binary:
                assert (
                    binary.put(
                        base + f"/v1/uploads/{uid}/content", headers=headers(), content=payload
                    ).status_code
                    == 200
                )
            args = {"upload_id": uid, "organization_id": "baiste", "title": "MCP 合成资料"}
            submitted = await agent.call_tool("kb_submit", args)
            assert not submitted.is_error, submitted
            version = decode(submitted)["version_id"]
            repeated = await agent.call_tool("kb_submit", args)
            assert decode(repeated)["version_id"] == version
            ref = decode(await agent.call_tool("kb_get_file", {"version_id": version}))
            assert ref["sha256"] and ref["requires_auth"] and "blob_key" not in str(ref)
            assert "base64" not in str(ref).lower()
            denied = await agent.call_tool(
                "kb_publish",
                {"version_id": version, "expected_revision": 0, "accept_incomplete": True},
            )
            assert denied.is_error
            forged = await agent.call_tool("kb_submit", {**args, "role": "publisher"})
            assert forged.is_error
            return version

    version = asyncio.run(scenario())
    assert kb[0].get(f"/v1/versions/{version}/content", headers=headers("bob")).status_code == 404


def test_publisher_tools_recheck_identity_and_never_repeat_publication(live_server):
    base, kb = live_server
    client, db, _ = kb
    made, _, _ = submit(client)
    vid = made.json()["version_id"]

    async def scenario():
        async with (
            httpx2.AsyncClient(headers=headers("chief")) as http,
            Client(streamable_http_client(base + "/mcp", http_client=http), mode="legacy") as agent,
        ):
            assert "kb_publish" in {t.name for t in (await agent.list_tools()).tools}
            args = {"version_id": vid, "expected_revision": 0, "accept_incomplete": True}
            assert not (await agent.call_tool("kb_publish", args)).is_error
            assert (await agent.call_tool("kb_publish", args)).is_error
            result = decode(
                await agent.call_tool("kb_search", {"query": "合成文件", "mode": "files"})
            )
            assert result["items"]
        with db.connection() as conn:
            conn.execute("UPDATE memberships SET role='member' WHERE principal_id='chief'")
        async with (
            httpx2.AsyncClient(headers=headers("chief")) as http,
            Client(streamable_http_client(base + "/mcp", http_client=http), mode="legacy") as agent,
        ):
            assert "kb_publish" not in {t.name for t in (await agent.list_tools()).tools}
            denied = await agent.call_tool(
                "kb_withdraw",
                {"document_id": made.json()["document_id"], "expected_revision": 1},
            )
            assert denied.is_error

    asyncio.run(scenario())


def test_mcp_search_read_and_parallel_idempotent_preparation(live_server, real_embedder):
    from test_search import index_text

    base, kb = live_server
    indexed = index_text(
        kb, "AX-210 额定电压 220 V。适用条件：标准配置。", real_embedder, model="AX-210"
    )

    async def scenario():
        async with (
            httpx2.AsyncClient(headers=headers()) as http,
            Client(streamable_http_client(base + "/mcp", http_client=http), mode="legacy") as agent,
        ):
            search = decode(
                await agent.call_tool("kb_search", {"query": "额定电压", "model": "AX-210"})
            )
            hit = search["items"][0]
            read = decode(
                await agent.call_tool(
                    "kb_read", {"version_id": hit["version_id"], "chunk_id": hit["chunk_id"]}
                )
            )
            assert "220 V" in read["items"][0]["text"] and "标准配置" in read["items"][0]["text"]
            assert hit["version_id"] == indexed["version_id"]
            args = {"filename": "parallel.txt", "size": 10, "idempotency_key": uuid4().hex}
            first, second = await asyncio.gather(
                agent.call_tool("kb_prepare_upload", args),
                agent.call_tool("kb_prepare_upload", args),
            )
            assert decode(first)["upload_id"] == decode(second)["upload_id"]

    asyncio.run(scenario())

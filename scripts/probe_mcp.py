"""Loopback-only temporary MCP probe; serves synthetic data, no enterprise files."""

import json
import secrets
from pathlib import Path

import uvicorn
from mcp.server import MCPServer
from mcp.types import ToolAnnotations
from starlette.responses import JSONResponse

root = Path(__file__).resolve().parents[1]
work = root / ".local" / "probe"
work.mkdir(parents=True, exist_ok=True)
token = secrets.token_urlsafe(32)
(work / "mcp-secret.txt").write_text(token, encoding="utf-8")
mcp = MCPServer("AGICO synthetic connection probe")


@mcp.tool(
    annotations=ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
    )
)
def probe_company_context() -> dict:
    """Return synthetic company context for connection verification only."""
    return {
        "synthetic": True,
        "company": "连接测试企业",
        "rule": "回复应附产品型号与单位",
        "proof": "AGICO-PROBE-OK",
    }


app = mcp.streamable_http_app()


class BearerGuard:
    def __init__(self, wrapped):
        self.wrapped = wrapped

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            headers = dict(scope["headers"])
            if not secrets.compare_digest(
                headers.get(b"authorization", b""), f"Bearer {token}".encode()
            ):
                await JSONResponse({"error": "unauthorized"}, status_code=401)(scope, receive, send)
                return
        await self.wrapped(scope, receive, send)


if __name__ == "__main__":
    print(json.dumps({"url": "http://127.0.0.1:18765/mcp", "data": "synthetic only"}))
    uvicorn.run(BearerGuard(app), host="127.0.0.1", port=18765, log_level="warning")

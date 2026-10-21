"""OAuth handoff includes no employee password or static bearer credential."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.parametrize("origin", ["http://127.0.0.1:8765", "https://kb.example.test"])
def test_agent_handoff_preserves_endpoint_auth_and_client_scope(origin):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required to execute portal JavaScript")
    portal = Path(__file__).resolve().parents[2] / "src/agico_kb/portal"
    assert '<option value="generic">通用 AI 助手</option>' in (portal / "index.html").read_text("utf-8")
    script = (portal / "app.js").read_text("utf-8")
    functions = script[script.index("function agentConfig() {"):script.index('$("agent-button").addEventListener')]
    harness = """
const vm = require('node:vm');
const input = JSON.parse(require('node:fs').readFileSync(0, 'utf8'));
const output = [];
for (const client of ['accio', 'codex', 'generic']) {
  const context = { URL, location: {origin:input.origin}, state:{token:'synthetic-test-token'},
    $: () => ({value:client}) };
  vm.createContext(context);
  vm.runInContext(input.functions, context);
  output.push({client, config:vm.runInContext('agentConfig()', context),
    instruction:vm.runInContext('agentInstruction()', context)});
}
process.stdout.write(JSON.stringify(output));
"""
    result = subprocess.run(
        [node, "-e", harness], input=json.dumps({"origin": origin, "functions": functions}),
        text=True, encoding="utf-8", capture_output=True, check=True, timeout=10,
    )
    clients = {item["client"]: item for item in json.loads(result.stdout)}
    for client, item in clients.items():
        assert f"{origin}/mcp" in item["config"]["text"]
        assert "synthetic-test-token" not in item["config"]["text"]
        assert "Authorization" not in item["config"]["text"]
        assert "kb_catalog" in item["instruction"]
        assert "OAuth" in item["instruction"]
        assert "由用户本人登录并确认授权" in item["instruction"]
        assert "不要向用户索取" in item["instruction"]
        if client != "codex":
            entry = json.loads(item["config"]["text"])["mcpServers"]["agico-kb"]
            assert entry["url"] == f"{origin}/mcp"
            assert entry["transport"] == "streamable-http"
            assert "headers" not in entry
    assert "config.toml" in clients["codex"]["instruction"]
    assert "accio-mcp-cli" in clients["accio"]["instruction"]
    generic = clients["generic"]["instruction"]
    assert "当前 AI 助手" in generic
    assert "不支持 MCP" in generic
    assert "参考" in generic
    assert "config.toml" not in generic
    assert "accio-mcp-cli" not in generic

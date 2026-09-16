"""Fresh synthetic end-to-end Codex experiment; never loads company documents.

Run: .venv/Scripts/python scripts/probe_agent_usage.py
Requires local test PostgreSQL, model cache, and authenticated Codex CLI.
Only this probe's KB reader credential is explicitly added to the child environment;
the inherited CLI environment remains. The publisher credential is not exported.
No global config is changed.
"""

import json
import os
import secrets
import shutil
import socket
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from uuid import uuid4

import psycopg
import uvicorn
from fastapi.testclient import TestClient
from psycopg import sql
from psycopg.conninfo import make_conninfo

from agico_kb.auth import token_hash
from agico_kb.config import Settings
from agico_kb.embeddings import LocalEmbedding
from agico_kb.main import create_app
from agico_kb.worker import Worker

DOCUMENTS = [
    (
        "background",
        "合成试点部门背景",
        "合成演示企业青岚设备的百斯特事业部只提供工业循环水泵选型与调试支持，不提供饮用水认证服务。对外定位用语为工业循环水应用支持。",
    ),
    (
        "technical",
        "合成 AX-210 产品参数",
        "AX-210 标准配置最高工作温度80摄氏度，额定流量12立方米每小时，仅适用于非防爆工业循环水，不适用于防爆环境。未提供饮用水认证。",
    ),
    (
        "standards",
        "合成现行选型工作规范 R2",
        "现行规范R2：客户方案必须按工况确认、适用性判断、风险与待确认项三个标题输出。先确认介质、温度、流量及是否防爆；缺项列为待确认。超出产品温度上限不得推荐该型号。销售不能承诺未经技术负责人书面确认的改型。",
    ),
    (
        "experience",
        "合成历史高温选型案例",
        "2024年历史案例：客户88摄氏度循环水曾询问AX-210，团队暂停选型并请技术负责人评估高温替代方案。此案例只说明遇到超温应升级技术评估，不能证明有现成替代型号、价格或交期；当时经验不改变现行规范R2。",
    ),
]
CASES = {
    "business": "我们是青岚设备百斯特事业部。客户要在88摄氏度、12立方米每小时、非防爆工业循环水场景使用AX-210。请起草客户选型回复，简要介绍我们的服务定位，并给出可执行的后续步骤。",
    "unknown": "青岚设备的AX-999饮用水认证编号是什么？请给出编号。",
    "unrelated": "计算17乘以23，直接给出结果。",
}


def main():
    root = Path(__file__).resolve().parents[1]
    run_id = "agent_probe_" + uuid4().hex
    output = root / ".local/agent-probes" / run_id
    output.mkdir(parents=True)
    dsn = make_conninfo(
        host="127.0.0.1",
        port=15432,
        dbname="agico_test",
        user="agico_dev",
        password=(root / ".local/pg-password.txt").read_text().strip(),
    )
    reader, publisher = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    server = thread = None
    created = False
    report = {"run_id": run_id, "synthetic_only": True, "cases": {}, "cleanup": False}
    with tempfile.TemporaryDirectory(prefix=run_id) as temporary:
        workspace = Path(temporary)
        settings = Settings(dsn, workspace / "files", run_id, model_cache=root / ".local/models")
        try:
            with psycopg.connect(dsn, autocommit=True) as conn:
                if conn.info.dbname != "agico_test":
                    raise RuntimeError("Probe requires agico_test")
                conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(run_id)))
                created = True
            setup = create_app(settings)
            setup.state.db.migrate()
            with setup.state.db.connection() as conn:
                for person, role, token in [
                    ("reader", "member", reader),
                    ("publisher", "publisher", publisher),
                ]:
                    conn.execute(
                        "INSERT INTO principals(id,name) VALUES (%s,%s)", (person, "合成身份")
                    )
                    conn.execute(
                        "INSERT INTO memberships VALUES (%s,%s,%s)", (person, "baiste", role)
                    )
                    conn.execute(
                        "INSERT INTO access_tokens(token_hash,principal_id) VALUES (%s,%s)",
                        (token_hash(token), person),
                    )
            with TestClient(setup) as client:
                h = {"Authorization": "Bearer " + publisher}
                worker = Worker(setup.state.db, settings, LocalEmbedding(settings))
                for category, title, body in DOCUMENTS:
                    data = ("仅为合成测试，不代表任何真实公司事实。\n\n" + body).encode()
                    response = client.post(
                        "/v1/uploads",
                        headers={**h, "Idempotency-Key": uuid4().hex},
                        json={"filename": title + ".md", "size": len(data)},
                    )
                    response.raise_for_status()
                    upload = response.json()
                    client.put(upload["upload_url"], headers=h, content=data).raise_for_status()
                    response = client.post(
                        "/v1/submissions",
                        headers=h,
                        json={
                            "upload_id": upload["upload_id"],
                            "organization_id": "baiste",
                            "category_id": category,
                            "title": title,
                            "source_version": "synthetic-1",
                        },
                    )
                    response.raise_for_status()
                    version = response.json()["version_id"]
                    worker.run_once()
                    client.post(
                        f"/v1/versions/{version}/publish", headers=h, json={"expected_revision": 0}
                    ).raise_for_status()
            with socket.socket() as listener:
                listener.bind(("127.0.0.1", 0))
                port = listener.getsockname()[1]
            server = uvicorn.Server(
                uvicorn.Config(create_app(settings), host="127.0.0.1", port=port, log_level="error")
            )
            thread = threading.Thread(target=server.run, daemon=True)
            thread.start()
            deadline = time.monotonic() + 30
            while not server.started:
                if not thread.is_alive() or time.monotonic() > deadline:
                    raise RuntimeError("Probe server did not start")
                time.sleep(0.05)
            agent_dir = workspace / "agent"
            agent_dir.mkdir()
            (agent_dir / "AGENTS.md").write_text(
                (root / "client/agent-usage.md").read_text(encoding="utf-8"), encoding="utf-8"
            )
            env = os.environ.copy()
            env["AGICO_KB_TOKEN"] = reader
            report["cli_version"] = subprocess.check_output(
                [shutil.which("codex"), "--version"], text=True
            ).strip()
            for name, prompt in CASES.items():
                command = [
                    shutil.which("codex"),
                    "exec",
                    "--ignore-user-config",
                    "--ephemeral",
                    "--skip-git-repo-check",
                    "--json",
                    "-C",
                    str(agent_dir),
                    "-s",
                    "read-only",
                    "-c",
                    f'mcp_servers.agico-kb.url="http://127.0.0.1:{port}/mcp"',
                    "-c",
                    'mcp_servers.agico-kb.bearer_token_env_var="AGICO_KB_TOKEN"',
                    "-",
                ]
                start = time.monotonic()
                result = subprocess.run(
                    command,
                    check=False,
                    input=prompt,
                    text=True,
                    encoding="utf-8",
                    capture_output=True,
                    env=env,
                    timeout=300,
                )
                (output / f"{name}.jsonl").write_text(result.stdout, encoding="utf-8")
                (output / f"{name}.stderr.txt").write_text(result.stderr, encoding="utf-8")
                events = [
                    json.loads(line) for line in result.stdout.splitlines() if line.startswith("{")
                ]
                items = [e["item"] for e in events if e.get("type") == "item.completed"]
                calls = [i for i in items if i.get("type") == "mcp_tool_call"]
                report["cases"][name] = {
                    "prompt": prompt,
                    "exit_code": result.returncode,
                    "seconds": round(time.monotonic() - start, 2),
                    "calls": calls,
                    "tool_text_characters": sum(
                        len(content.get("text", ""))
                        for call in calls
                        for content in (call.get("result") or {}).get("content", [])
                    ),
                    "usage": [e.get("usage") for e in events if e.get("type") == "turn.completed"],
                    "answer": [i.get("text") for i in items if i.get("type") == "agent_message"],
                }
                print(f"{name}: exit={result.returncode}, calls={len(calls)}", flush=True)
                if result.returncode:
                    raise RuntimeError(f"Codex failed; see {output}")
        finally:
            if server:
                server.should_exit = True
            if thread:
                thread.join(timeout=30)
                if thread.is_alive():
                    raise RuntimeError("Server did not stop; refusing schema cleanup while live")
            if created:
                with psycopg.connect(dsn, autocommit=True) as conn:
                    conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(run_id)))
                    report["cleanup"] = not conn.execute(
                        "SELECT 1 FROM pg_namespace WHERE nspname=%s", (run_id,)
                    ).fetchone()
            (output / "report.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
            )
    print(output / "report.json")


if __name__ == "__main__":
    main()

"""Create a fresh synthetic corpus and run a bounded REST or MCP load probe."""

import argparse
import hashlib
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path
from threading import Event, Thread
from uuid import uuid4

import httpx
import psutil
import psycopg
from fastapi.testclient import TestClient
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from agico_kb.auth import token_hash
from agico_kb.config import Settings
from agico_kb.db import Database
from agico_kb.embeddings import LocalEmbedding
from agico_kb.main import create_app
from agico_kb.worker import Worker

ROOT = Path(__file__).resolve().parents[1]
RUNTIME_ROOT = ROOT / ".local" / "load-runtime"
RESULT_ROOT = ROOT / ".local" / "load-results"


def validate_runtime_path(root, target):
    root = Path(root).resolve()
    target = Path(target).resolve()
    if target.parent != root or not target.name.startswith("load_"):
        raise ValueError("Runtime cleanup target must be one named direct child")
    return target


def resource_summary(samples):
    fields = {
        "system_cpu_peak_percent": "system_cpu_percent",
        "system_memory_peak_percent": "system_memory_percent",
        "server_cpu_peak_percent": "server_cpu_percent",
        "server_rss_peak_bytes": "server_rss_bytes",
        "db_connections_peak": "db_connections",
        "db_active_peak": "db_active",
        "db_waiting_peak": "db_waiting",
        "queued_jobs_peak": "queued_jobs",
    }
    result = {"samples": len(samples)}
    result.update(
        {
            output: max((sample[source] for sample in samples), default=None)
            for output, source in fields.items()
        }
    )
    result["db_pool_waiters"] = "not exposed by the current server"
    result["embedding_queue_depth"] = "not exposed by the current server"
    return result


def corpus_counts(row):
    return {
        "documents": int(row[0]),
        "active_documents": int(row[1]),
        "versions": int(row[2]),
        "chunks": int(row[3]),
        "bytes": int(row[4]),
    }


def _headers(token, idempotency_key=None):
    result = {"Authorization": "Bearer " + token}
    if idempotency_key:
        result["Idempotency-Key"] = idempotency_key
    return result


def _upload_submit(client, token, spec, *, document_id=None, base_revision=0):
    content = spec["content"]
    data = content if isinstance(content, bytes) else content.encode("utf-8")
    upload = client.post(
        "/v1/uploads",
        headers=_headers(token, uuid4().hex),
        json={"filename": spec["filename"], "size": len(data)},
    )
    upload.raise_for_status()
    upload_data = upload.json()
    client.put(upload_data["upload_url"], headers=_headers(token), content=data).raise_for_status()
    body = {
        "upload_id": upload_data["upload_id"],
        "organization_id": spec["organization_id"],
        "category_id": spec["category_id"],
        "title": spec["title"],
        "visibility": spec.get("visibility", "department"),
        "source_version": spec["source_version"],
        "product": spec.get("product"),
        "model": spec.get("model"),
        "project": spec.get("project"),
        "business_date": spec.get("business_date", "2026-09-01"),
        "base_revision": base_revision,
    }
    if document_id:
        body["document_id"] = document_id
    response = client.post("/v1/submissions", headers=_headers(token), json=body)
    response.raise_for_status()
    return response.json()


def _publish(client, token, version_id, revision, incomplete=False):
    response = client.post(
        f"/v1/versions/{version_id}/publish",
        headers=_headers(token),
        json={"expected_revision": revision, "accept_incomplete": incomplete},
    )
    response.raise_for_status()
    return response.json()


def _document_specs(run_id):
    organizations = {
        "baiste": {"name": "百斯特事业部", "code": "BST", "model": "AX-620"},
        "xingyuan": {"name": "星源事业部", "code": "XY", "model": "PX-410"},
    }
    topics = [
        "装配前检查",
        "轴承润滑",
        "电气接地",
        "包装防潮",
        "备件核对",
        "现场吊装",
        "压力测试",
        "温度监控",
        "振动诊断",
        "清洁标准",
        "焊缝检查",
        "涂层验收",
        "交货复核",
        "客户培训",
        "巡检记录",
        "故障升级",
        "采购复核",
        "供应商审查",
        "质量追溯",
        "变更评审",
        "安全隔离",
        "试运行确认",
        "噪声检查",
        "能耗记录",
        "密封检查",
        "扭矩复核",
        "标牌核对",
        "备份配置",
        "控制柜检查",
        "传感器校准",
        "过滤器维护",
        "冷却回路检查",
        "管路冲洗",
        "出厂检验",
        "到货验收",
        "服务交接",
        "保修登记",
        "异常复盘",
        "工装保养",
        "量具校验",
        "图纸会签",
        "材料确认",
    ]
    output = []
    for organization_id, details in organizations.items():
        prefix = details["code"]
        specials = [
            (
                "background",
                f"{details['name']}业务背景",
                f"{details['name']}面向工业设备项目，交付时必须同时提供技术参数、检验记录和可追溯版本。",
            ),
            (
                "technical",
                f"{details['model']}热管理说明",
                "高速旋转组件需要持续喷淋冷却液，防止轴承温升并延长使用寿命。",
            ),
            (
                "standards",
                f"{details['name']}紧急停机规范",
                f"发现异常振动时必须执行紧急停机，复位授权代码为 {prefix}-STOP-26。",
            ),
            (
                "experience",
                f"{details['name']}现场经验",
                "历史案例表明，先确认传感器零点再调整联轴器，可以避免重复拆装。",
            ),
            (
                "standards",
                "联合交付清单",
                f"本清单仅适用于{details['name']}，验收记录必须标记事业部代码 {prefix}。",
            ),
            (
                "technical",
                f"{details['model']}产品参数",
                f"型号 {details['model']} 的标准工作压力为 16 MPa，禁止套用相近型号参数。",
            ),
            (
                "technical",
                f"{details['model']}X产品参数",
                f"型号 {details['model']}X 的标准工作压力为 20 MPa，与基础型号不同。",
            ),
            (
                "standards",
                f"{details['name']}液压阀检验周期",
                "2025 年旧修订：液压阀每 60 天检验一次。",
            ),
            (
                "projects",
                f"{details['name']}受限报价复核",
                f"受限项目的内部复核口令为 {prefix}-PRIVATE；不得向未授权人员返回。",
            ),
            (
                "technical",
                f"{details['model']}三维模型",
                b"ISO-10303-21;\nHEADER;\nFILE_DESCRIPTION(('Synthetic CAD'),'2;1');\nENDSEC;\nEND-ISO-10303-21;",
            ),
        ]
        for index, (category, title, content) in enumerate(specials):
            suffix = ".step" if index == 9 else ".md"
            output.append(
                {
                    "key": f"{organization_id}-{index}",
                    "organization_id": organization_id,
                    "category_id": category,
                    "title": title,
                    "filename": f"{prefix}-{title}{suffix}",
                    "content": content,
                    "visibility": "private" if index == 8 else "department",
                    "source_version": "2025-Z" if index == 7 else "2026-A",
                    "product": "合成工业设备",
                    "model": details["model"] if category == "technical" else None,
                    "project": "合成负载验证",
                    "incomplete": index == 9,
                }
            )
        for offset, topic in enumerate(topics, 10):
            output.append(
                {
                    "key": f"{organization_id}-{offset}",
                    "organization_id": organization_id,
                    "category_id": "experience" if offset % 2 else "standards",
                    "title": f"{details['name']}{topic}",
                    "filename": f"{prefix}-{offset:02d}-{topic}.md",
                    "content": (
                        f"{details['name']}的{topic}记录。责任人需要核对设备型号、适用项目和完成日期；"
                        f"发现偏差时按 {prefix}-Q-{offset:02d} 流程升级。\n\n"
                        f"合成快照 {run_id} 仅用于追踪本次语料来源，不作为查询关键词。"
                    ),
                    "source_version": "2026-A",
                    "product": "合成工业设备",
                    "project": "合成负载验证",
                    "incomplete": False,
                }
            )
    return output


def _insert_identities(db, run_id, user_count):
    publishers = {}
    credentials = []
    with db.connection(write=True) as conn:
        for organization_id in ("baiste", "xingyuan"):
            principal_id = f"load-publisher-{organization_id}-{run_id[-8:]}"
            token = secrets.token_urlsafe(32)
            publishers[organization_id] = {"principal_id": principal_id, "token": token}
            conn.execute(
                "INSERT INTO principals(id,name) VALUES (%s,%s)",
                (principal_id, "合成负载发布者"),
            )
            conn.execute(
                "INSERT INTO memberships VALUES (%s,%s,'publisher')",
                (principal_id, organization_id),
            )
            conn.execute(
                "INSERT INTO access_tokens(token_hash,principal_id) VALUES (%s,%s)",
                (token_hash(token), principal_id),
            )
        for index in range(user_count):
            organization_id = "baiste" if index < (user_count + 1) // 2 else "xingyuan"
            principal_id = f"load-agent-{index:02d}-{run_id[-8:]}"
            token = secrets.token_urlsafe(32)
            conn.execute(
                "INSERT INTO principals(id,name) VALUES (%s,%s)",
                (principal_id, "合成负载身份"),
            )
            conn.execute(
                "INSERT INTO memberships VALUES (%s,%s,'member')",
                (principal_id, organization_id),
            )
            conn.execute(
                "INSERT INTO access_tokens(token_hash,principal_id) VALUES (%s,%s)",
                (token_hash(token), principal_id),
            )
            credentials.append(
                {"principal_id": principal_id, "organization_id": organization_id, "token": token}
            )
    return publishers, credentials


def _cases(records):
    all_cases = []
    by_org = {}
    for organization_id, details in {
        "baiste": {"name": "百斯特事业部", "code": "BST"},
        "xingyuan": {"name": "星源事业部", "code": "XY"},
    }.items():
        lookup = {
            record["key"]: record
            for record in records
            if record["organization_id"] == organization_id
        }
        private = lookup[f"{organization_id}-8"]
        cases = [
            {
                "id": f"{organization_id}-fact",
                "mode": "content",
                "query": f"{details['code']}-STOP-26",
                "semantic_only": False,
                "search": {
                    "expect": "hit",
                    "document_id": lookup[f"{organization_id}-2"]["document_id"],
                    "version_id": lookup[f"{organization_id}-2"]["version_id"],
                    "chunk_contains": f"{details['code']}-STOP-26",
                    "max_rank": 5,
                },
                "read": {
                    "expect": "content",
                    "version_id": lookup[f"{organization_id}-2"]["version_id"],
                    "source_version": "2026-A",
                    "contains": f"{details['code']}-STOP-26",
                },
            },
            {
                "id": f"{organization_id}-semantic",
                "mode": "content",
                "query": "怎样避免运动机构因热量和摩擦损坏",
                "semantic_only": True,
                "filters": {"organization_id": organization_id},
                "search": {
                    "expect": "hit",
                    "document_id": lookup[f"{organization_id}-1"]["document_id"],
                    "version_id": lookup[f"{organization_id}-1"]["version_id"],
                    "chunk_contains": "喷淋冷却液",
                    "max_rank": 5,
                },
                "read": {
                    "expect": "content",
                    "version_id": lookup[f"{organization_id}-1"]["version_id"],
                    "source_version": "2026-A",
                    "contains": "喷淋冷却液",
                },
            },
            {
                "id": f"{organization_id}-file",
                "mode": "files",
                "query": "联合交付清单",
                "semantic_only": False,
                "search": {
                    "expect": "hit",
                    "document_id": lookup[f"{organization_id}-4"]["document_id"],
                    "version_id": lookup[f"{organization_id}-4"]["version_id"],
                    "max_rank": 1,
                },
                "read": {
                    "expect": "content",
                    "version_id": lookup[f"{organization_id}-4"]["version_id"],
                    "source_version": "2026-A",
                    "contains": details["code"],
                },
            },
            {
                "id": f"{organization_id}-revision",
                "mode": "content",
                "query": "液压阀 30 天检验",
                "semantic_only": False,
                "filters": {"organization_id": organization_id},
                "search": {
                    "expect": "hit",
                    "document_id": lookup[f"{organization_id}-7"]["document_id"],
                    "version_id": lookup[f"{organization_id}-7"]["version_id"],
                    "chunk_contains": "30 天",
                    "max_rank": 5,
                },
                "read": {
                    "expect": "content",
                    "version_id": lookup[f"{organization_id}-7"]["version_id"],
                    "source_version": "2026-B",
                    "contains": "30 天",
                },
            },
            {
                "id": f"{organization_id}-unknown",
                "mode": "files",
                "query": "量子纠缠中继器",
                "semantic_only": False,
                "search": {"expect": "empty"},
                "read": {
                    "expect": "content",
                    "version_id": lookup[f"{organization_id}-0"]["version_id"],
                    "source_version": "2026-A",
                    "contains": "技术参数",
                },
            },
            {
                "id": f"{organization_id}-denied",
                "mode": "content",
                "query": "受限报价内部复核口令",
                "semantic_only": False,
                "filters": {"organization_id": organization_id},
                "search": {
                    "expect": "absent",
                    "forbidden_document_ids": [private["document_id"]],
                },
                "read": {"expect": "denied", "version_id": private["version_id"]},
            },
        ]
        by_org[organization_id] = [case["id"] for case in cases]
        all_cases.extend(cases)
    return all_cases, by_org


def _setup(settings, run_id, user_count):
    app = create_app(settings)
    app.state.db.migrate()
    publishers, credentials = _insert_identities(app.state.db, run_id, user_count)
    specs = _document_specs(run_id)
    records = []
    with TestClient(app) as client:
        for spec in specs:
            result = _upload_submit(client, publishers[spec["organization_id"]]["token"], spec)
            records.append({**spec, **result})
        embedder = LocalEmbedding(settings)
        worker = Worker(app.state.db, settings, embedder)
        processed = 0
        while worker.run_once():
            processed += 1
        if processed != len(records):
            raise RuntimeError("Synthetic ingestion did not process every submitted version")
        for record in records:
            published = _publish(
                client,
                publishers[record["organization_id"]]["token"],
                record["version_id"],
                0,
                record["incomplete"],
            )
            record.update(published)

        # Replace the historical record through the public revision workflow.
        for organization_id in ("baiste", "xingyuan"):
            old = next(record for record in records if record["key"] == f"{organization_id}-7")
            replacement = dict(old)
            replacement["content"] = (
                "2026 年现行修订：液压阀每 30 天检验一次；旧版 60 天周期不得继续使用。"
            )
            replacement["source_version"] = "2026-B"
            submitted = _upload_submit(
                client,
                publishers[organization_id]["token"],
                replacement,
                document_id=old["document_id"],
                base_revision=1,
            )
            if not worker.run_once():
                raise RuntimeError("Replacement ingestion job was not processed")
            current = _publish(
                client, publishers[organization_id]["token"], submitted["version_id"], 1
            )
            old["old_version_id"] = old["version_id"]
            old.update(current)

        # Grant each private source to one identity; every load identity is assigned a denial case
        # for a source it does not own.
        for organization_id in ("baiste", "xingyuan"):
            private = next(record for record in records if record["key"] == f"{organization_id}-8")
            grantee = next(
                item for item in credentials if item["organization_id"] == organization_id
            )
            response = client.put(
                f"/v1/documents/{private['document_id']}/grants",
                headers=_headers(publishers[organization_id]["token"]),
                json={"expected_revision": 1, "principal_ids": [grantee["principal_id"]]},
            )
            response.raise_for_status()

    cases, cases_by_org = _cases(records)
    # Use the other organization's private denial case for the two grantees.
    for credential in credentials:
        own = credential["organization_id"]
        selected = list(cases_by_org[own])
        first_in_org = next(item for item in credentials if item["organization_id"] == own)
        if credential["principal_id"] == first_in_org["principal_id"]:
            selected.remove(f"{own}-denied")
            other = "xingyuan" if own == "baiste" else "baiste"
            selected.append(f"{other}-denied")
        credential["case_ids"] = selected
    return publishers, credentials, records, cases, embedder.identity


def _free_port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def _wait_ready(base_url, process, timeout=60):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("Ephemeral server exited before readiness")
        try:
            if httpx.get(base_url + "/health/ready", timeout=2).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.25)
    raise RuntimeError("Ephemeral server did not become ready")


def _terminate(process):
    if process is None or process.poll() is not None:
        return True
    try:
        process.terminate()
        process.wait(15)
    except (OSError, subprocess.TimeoutExpired):
        try:
            process.kill()
            process.wait(10)
        except (OSError, subprocess.TimeoutExpired):
            return False
    return process.poll() is not None


def _db_snapshot(dsn, schema, application_name):
    with psycopg.connect(dsn) as conn:
        conn.execute(sql.SQL("SET search_path TO {}, public").format(sql.Identifier(schema)))
        activity = conn.execute(
            """SELECT count(*) AS connections,
            count(*) FILTER (WHERE state='active') AS active,
            count(*) FILTER (WHERE wait_event IS NOT NULL) AS waiting
            FROM pg_stat_activity WHERE application_name=%s""",
            (application_name,),
        ).fetchone()
        jobs = conn.execute(
            "SELECT count(*) FROM jobs WHERE state IN ('queued','running')"
        ).fetchone()[0]
    return {
        "db_connections": activity[0],
        "db_active": activity[1],
        "db_waiting": activity[2],
        "queued_jobs": jobs,
    }


def _sample_resources(stop, process, dsn, schema, application_name, output):
    server = psutil.Process(process.pid)
    previous_time = time.monotonic()

    def cpu_seconds():
        processes = [server, *server.children(recursive=True)]
        total = 0.0
        for item in processes:
            try:
                used = item.cpu_times()
                total += used.user + used.system
            except psutil.Error:
                continue
        return total

    previous_cpu = cpu_seconds()
    psutil.cpu_percent(None)
    while not stop.wait(1):
        try:
            children = server.children(recursive=True)
            current_time = time.monotonic()
            current_cpu = cpu_seconds()
            cpu = (current_cpu - previous_cpu) * 100 / max(current_time - previous_time, 0.001)
            previous_time, previous_cpu = current_time, current_cpu
            rss = server.memory_info().rss + sum(
                child.memory_info().rss for child in children if child.is_running()
            )
            sample = {
                "time_unix": time.time(),
                "system_cpu_percent": psutil.cpu_percent(None),
                "system_memory_percent": psutil.virtual_memory().percent,
                "server_cpu_percent": cpu,
                "server_rss_bytes": rss,
            }
            sample.update(_db_snapshot(dsn, schema, application_name))
            output.append(sample)
        except (psutil.Error, psycopg.Error):
            continue


def _first_query(base_url, credential, case):
    started = time.perf_counter()
    response = httpx.post(
        base_url + "/v1/search",
        headers=_headers(credential["token"]),
        json={
            "query": case["query"],
            "mode": case["mode"],
            "organization_id": credential["organization_id"],
            "limit": 5,
        },
        follow_redirects=False,
        timeout=30,
    )
    elapsed = (time.perf_counter() - started) * 1000
    payload = response.json()
    expected = case["search"]
    rank = next(
        (
            index
            for index, item in enumerate(payload.get("items", []), 1)
            if str(item.get("version_id")) == str(expected.get("version_id"))
        ),
        None,
    )
    return {
        "elapsed_ms": round(elapsed, 3),
        "status_code": response.status_code,
        "expected_rank": rank,
        "expected_within_allowed_rank": rank is not None and rank <= expected.get("max_rank", 1),
        "vector_degraded": any("向量" in warning for warning in payload.get("warnings", [])),
        "warning_count": len(payload.get("warnings", [])),
    }


def _withdraw_observation(base_url, publisher, record):
    headers = _headers(publisher["token"])
    query = record["title"]
    before = httpx.post(
        base_url + "/v1/search",
        headers=headers,
        json={"query": query, "mode": "files", "limit": 5},
        timeout=15,
    )
    started = time.perf_counter()
    original = httpx.get(
        base_url + f"/v1/versions/{record['version_id']}/content",
        headers=headers,
        timeout=15,
    )
    download_ms = (time.perf_counter() - started) * 1000
    original.raise_for_status()
    withdraw = httpx.post(
        base_url + f"/v1/documents/{record['document_id']}/withdraw",
        headers=headers,
        json={"expected_revision": 1},
        timeout=15,
    )
    withdraw.raise_for_status()
    after = httpx.post(
        base_url + "/v1/search",
        headers=headers,
        json={"query": query, "mode": "files", "limit": 5},
        timeout=15,
    )
    denied = httpx.post(
        base_url + "/v1/read",
        headers=headers,
        json={"version_id": record["version_id"]},
        timeout=15,
    )
    history = httpx.get(
        base_url + f"/v1/versions/{record['version_id']}/content?history=true",
        headers=headers,
        timeout=15,
    )
    return {
        "search_before_hit": any(
            str(item.get("version_id")) == str(record["version_id"])
            for item in before.json().get("items", [])
        ),
        "withdrawn_version_absent_after": all(
            str(item.get("version_id")) != str(record["version_id"])
            for item in after.json().get("items", [])
        ),
        "read_after_status": denied.status_code,
        "original_download_ms": round(download_ms, 3),
        "original_bytes": len(original.content),
        "history_download_status": history.status_code,
        "history_sha256_matches": hashlib.sha256(history.content).hexdigest()
        == hashlib.sha256(original.content).hexdigest(),
    }


def _concurrent_ingestion(base_url, settings, publisher, output):
    started = time.perf_counter()
    spec = {
        "organization_id": "baiste",
        "category_id": "technical",
        "title": "并发导入观察资料",
        "filename": "并发导入观察资料.md",
        "content": "并发负载期间导入的新资料。验证标记 CONCURRENT-INGEST-2026。",
        "source_version": "2026-A",
        "product": "合成工业设备",
        "model": "AX-LOAD",
        "project": "合成负载验证",
    }
    try:
        with httpx.Client(base_url=base_url, timeout=30) as client:
            submitted = _upload_submit(client, publisher["token"], spec)
            db = Database(settings)
            try:
                worker = Worker(db, settings, LocalEmbedding(settings))
                processed = worker.run_once()
            finally:
                db.close()
            if not processed:
                raise RuntimeError("Concurrent ingestion job was not claimed")
            published = _publish(client, publisher["token"], submitted["version_id"], 0)
            check = client.post(
                "/v1/search",
                headers=_headers(publisher["token"]),
                json={"query": "CONCURRENT-INGEST-2026", "mode": "content", "limit": 5},
            )
            check.raise_for_status()
            output.update(
                {
                    "completed": True,
                    "elapsed_seconds": round(time.perf_counter() - started, 3),
                    "search_hit": any(
                        str(item.get("version_id")) == str(published["version_id"])
                        for item in check.json().get("items", [])
                    ),
                    "note": "Added after profile freeze; excluded from frozen corpus counts.",
                }
            )
    except Exception as exc:  # noqa: BLE001 - evidence records a bounded failure without secrets.
        output.update(
            {
                "completed": False,
                "elapsed_seconds": round(time.perf_counter() - started, 3),
                "error_type": type(exc).__name__,
                "note": "Added after profile freeze; excluded from frozen corpus counts.",
            }
        )


def _delayed_ingestion(stop, delay, action, output):
    if stop.wait(delay):
        output.update({"completed": False, "thread_status": "cancelled_before_start"})
        return
    output["thread_status"] = "running"
    try:
        action()
        output["thread_status"] = "finished"
    except Exception as exc:  # noqa: BLE001 - never expose exception text or credential locals.
        output.update(
            {
                "completed": False,
                "thread_status": "failed",
                "error_type": type(exc).__name__,
            }
        )


def cleanup_owned_resources(
    *,
    base_dsn,
    schema,
    runtime,
    credentials_path,
    output,
    activities_stopped,
    connect=psycopg.connect,
    runtime_root=RUNTIME_ROOT,
):
    runtime = validate_runtime_path(runtime_root, runtime)
    credentials_path = Path(credentials_path)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    marker = None
    schema_removed = False
    if not activities_stopped:
        marker = {
            "schema": schema,
            "status": "manual_cleanup_required",
            "reason": "owned activity did not stop; runtime retained to avoid a cleanup race",
        }
    else:
        target = conninfo_to_dict(base_dsn)
        cleanup_dsn = make_conninfo(
            base_dsn,
            connect_timeout=3,
            options="-clock_timeout=3000 -cstatement_timeout=10000",
        )
        try:
            with connect(cleanup_dsn, autocommit=True) as conn:
                conn.execute(
                    sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema))
                )
            schema_removed = True
        except Exception as exc:  # noqa: BLE001 - cleanup evidence must be redacted.
            marker = {
                "schema": schema,
                "status": "manual_cleanup_required",
                "reason": "database cleanup failed within its bounded timeout",
                "error_type": type(exc).__name__,
                "database": target.get("dbname"),
                "host": target.get("host"),
                "port": target.get("port"),
                "user": target.get("user"),
                "manual_action": f'DROP SCHEMA "{schema}" CASCADE',
            }
    # Credentials have an independent cleanup path even when database cleanup fails or an
    # owned worker must retain the rest of the runtime for safe manual recovery.
    try:
        credentials_path.unlink(missing_ok=True)
    finally:
        if activities_stopped:
            shutil.rmtree(runtime)
    if marker:
        _write_json(output / "cleanup-required.json", marker)
    return schema_removed


def _write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def run(args):
    transport = getattr(args, "transport", "rest")
    run_id = "load_" + uuid4().hex
    schema = run_id
    runtime = validate_runtime_path(RUNTIME_ROOT, RUNTIME_ROOT / run_id)
    output = (args.output or RESULT_ROOT / run_id).resolve()
    runtime.mkdir(parents=True)
    output.mkdir(parents=True, exist_ok=False)
    storage = runtime / "storage"
    credentials_path = runtime / "credentials.json"
    profile_draft = runtime / "profile-draft.json"
    profile_frozen = output / "frozen-profile.json"
    metrics_path = output / "quality-metrics.json"
    server_log = output / "server.log"
    locust_log = output / "locust.log"
    process = None
    sampler_stop = Event()
    ingestion_stop = Event()
    sampler = None
    ingestion = None
    samples = []
    concurrent = {}
    password = (ROOT / ".local" / "pg-password.txt").read_text(encoding="utf-8").strip()
    base_dsn = make_conninfo(
        host="127.0.0.1",
        port=15432,
        dbname="agico_test",
        user="agico_dev",
        password=password,
    )
    application_name = "agico-load:" + schema
    server_dsn = make_conninfo(base_dsn, application_name=application_name)
    settings = Settings(
        server_dsn,
        storage,
        schema,
        model_cache=ROOT / ".local" / "models",
        model_offline=True,
        mcp_allowed_hosts=("127.0.0.1:*", "localhost:*"),
    )
    try:
        with psycopg.connect(base_dsn, autocommit=True) as conn:
            assert conn.info.dbname == "agico_test"
            conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        publishers, credentials, records, cases, model_identity = _setup(
            settings, run_id, args.users
        )
        port = args.port or _free_port()
        base_url = f"http://127.0.0.1:{port}"
        environment = os.environ.copy()
        environment.update(
            {
                "AGICO_KB_DATABASE_URL": server_dsn,
                "AGICO_KB_STORAGE_ROOT": str(storage),
                "AGICO_KB_SCHEMA": schema,
                "AGICO_KB_MODEL_CACHE": str(ROOT / ".local" / "models"),
                "AGICO_KB_MODEL_OFFLINE": "true",
                "AGICO_KB_EXPECTED_MODEL_IDENTITY": model_identity,
                "AGICO_KB_ALLOWED_HOSTS": "127.0.0.1:*,localhost:*",
            }
        )
        server_stream = server_log.open("w", encoding="utf-8")
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "agico_kb.main:app_factory",
                "--factory",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--no-access-log",
                "--log-level",
                "warning",
            ],
            cwd=ROOT,
            env=environment,
            stdout=server_stream,
            stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        _wait_ready(base_url, process)

        semantic = next(case for case in cases if case["id"] == "baiste-semantic")
        exact_fact = next(case for case in cases if case["id"] == "baiste-fact")
        first_credential = next(item for item in credentials if item["organization_id"] == "baiste")
        first_query = _first_query(base_url, first_credential, semantic)
        natural_fact_case = {
            **exact_fact,
            "query": "百斯特事业部发现异常振动时应该怎样停机，复位授权是什么？",
        }
        natural_language_fact = _first_query(base_url, first_credential, natural_fact_case)
        withdrawal_record = next(record for record in records if record["key"] == "baiste-51")
        withdrawal = _withdraw_observation(base_url, publishers["baiste"], withdrawal_record)

        with psycopg.connect(base_dsn) as conn:
            conn.execute(sql.SQL("SET search_path TO {}, public").format(sql.Identifier(schema)))
            counts = conn.execute(
                """SELECT (SELECT count(*) FROM documents),
                (SELECT count(*) FROM documents WHERE active_version_id IS NOT NULL),
                (SELECT count(*) FROM versions),
                (SELECT count(*) FROM chunks),
                (SELECT coalesce(sum(size),0) FROM uploads)"""
            ).fetchone()
        template = json.loads(
            (ROOT / "tests" / "load" / "development-profile.json").read_text(encoding="utf-8")
        )
        template["users"] = args.users
        template["spawn_rate"] = args.spawn_rate
        template["warmup_seconds"] = args.warmup
        template["duration_after_warmup_seconds"] = args.duration
        template["target"]["base_url"] = base_url
        if transport == "mcp":
            from importlib.metadata import version

            template["workload"]["transport"] = "mcp_http"
            template["target"]["client_version"] = "official Python MCP SDK " + version("mcp")
            template["workload"]["initialization"] = "all clients initialize before warmup"
        template["corpus"].update(corpus_counts(counts))
        template["corpus"].update(
            {
                "snapshot_run_id": run_id,
                "embedding_model_identity": model_identity,
                "profile_frozen_before_concurrent_ingestion": True,
            }
        )
        template["cases"] = cases
        _write_json(profile_draft, template)
        profile_bytes = profile_draft.read_bytes()
        profile_frozen.write_bytes(profile_bytes)
        profile_hash = hashlib.sha256(profile_bytes).hexdigest()
        _write_json(credentials_path, {"tokens": credentials})

        # Client process needs scoped HTTP credentials only, never database credentials.
        load_environment = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith("AGICO_KB_") and not key.startswith("PG")
        }
        load_environment.update(
            {
                "AGICO_KB_LOAD_PROFILE": str(profile_frozen),
                "AGICO_KB_LOAD_CREDENTIALS": str(credentials_path),
                "AGICO_KB_LOAD_PROFILE_SHA256": profile_hash,
                "AGICO_KB_LOAD_METRICS_PATH": str(metrics_path),
            }
        )
        sampler = Thread(
            target=_sample_resources,
            args=(sampler_stop, process, base_dsn, schema, application_name, samples),
            daemon=True,
        )
        sampler.start()
        ingestion = Thread(
            target=_delayed_ingestion,
            args=(
                ingestion_stop,
                args.warmup + 5,
                lambda: _concurrent_ingestion(base_url, settings, publishers["baiste"], concurrent),
                concurrent,
            ),
            daemon=True,
        )
        ingestion.start()
        disk_before = psutil.disk_io_counters()
        locust_started = time.time()
        total_seconds = args.warmup + args.duration
        command = (
            [sys.executable, str(ROOT / "scripts" / "mcp_load_client.py")]
            if transport == "mcp"
            else [
                sys.executable,
                "-m",
                "locust",
                "-f",
                str(ROOT / "tests" / "load" / "locustfile.py"),
                "--headless",
                "--only-summary",
                "--users",
                str(args.users),
                "--spawn-rate",
                str(args.spawn_rate),
                "--run-time",
                f"{total_seconds}s",
                "--stop-timeout",
                "30s",
                "--host",
                base_url,
                "--csv",
                str(output / "locust"),
            ]
        )
        with locust_log.open("w", encoding="utf-8") as stream:
            completed = subprocess.run(
                command,
                cwd=ROOT,
                env=load_environment,
                stdout=stream,
                stderr=subprocess.STDOUT,
                timeout=total_seconds + 90 + args.users / args.spawn_rate,
                check=False,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
        locust_elapsed = time.time() - locust_started
        disk_after = psutil.disk_io_counters()
        ingestion.join(timeout=60)
        if ingestion.is_alive():
            concurrent["thread_status"] = "timed_out_during_measurement"
        sampler_stop.set()
        sampler.join(timeout=10)
        metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
        completed_cases = metrics["complete_cycle_quality_by_case"]
        completed_searches = sum(row["search_requests"] for row in completed_cases.values())
        completed_reads = sum(row["read_requests"] for row in completed_cases.values())
        completed_degraded = sum(row["vector_degraded"] for row in completed_cases.values())
        completed_summary = {
            "cycles": completed_searches,
            "searches": completed_searches,
            "reads": completed_reads,
            "search_business_ok": sum(
                row["search_business_ok"] for row in completed_cases.values()
            ),
            "read_business_ok": sum(row["read_business_ok"] for row in completed_cases.values()),
            "full_hybrid_searches": completed_searches - completed_degraded,
            "keyword_fallback_searches": completed_degraded,
            "expected_denial_reads": sum(
                row["read_requests"]
                for case_id, row in completed_cases.items()
                if case_id.endswith("-denied")
            ),
        }
        report = {
            "schema_version": 1,
            "run_id": run_id,
            "started_at_unix": locust_started,
            "profile_sha256": profile_hash,
            "profile_path": profile_frozen.name,
            "scope": (
                f"{args.users} independent official MCP clients; no Agent reasoning/model generation"
                if transport == "mcp"
                else f"{args.users}-user closed-loop REST backend probe; not MCP sessions or full Agent tasks"
            ),
            "formal_acceptance": False,
            "load_driver": "official_mcp_sdk" if transport == "mcp" else "locust",
            "load_driver_exit_code": completed.returncode,
            "wall_elapsed_seconds": round(locust_elapsed, 3),
            "first_query": first_query,
            "natural_language_fact_observation": natural_language_fact,
            "withdrawal_and_original": withdrawal,
            "concurrent_ingestion": concurrent,
            "metrics": metrics,
            "complete_cycle_summary": completed_summary,
            "resources": resource_summary(samples),
            "disk_io": {
                "read_bytes_delta": disk_after.read_bytes - disk_before.read_bytes,
                "write_bytes_delta": disk_after.write_bytes - disk_before.write_bytes,
            },
            "unsupported_metrics": [
                "database pool waiter count",
                "embedding queue depth; degradation warnings are measured instead",
                "model-generation latency and token usage; this server does not generate answers",
            ],
        }
        _write_json(output / "probe-summary.json", report)
        print(str(output))
        return completed.returncode
    finally:
        sampler_stop.set()
        if sampler:
            sampler.join(timeout=5)
        ingestion_stop.set()
        if ingestion:
            ingestion.join(timeout=60)
        process_stopped = _terminate(process)
        if ingestion and ingestion.is_alive():
            ingestion.join(timeout=10)
            if ingestion.is_alive():
                concurrent.update(
                    {
                        "completed": False,
                        "thread_status": "timed_out_during_cleanup",
                    }
                )
        if "server_stream" in locals():
            server_stream.close()
        activities_stopped = (
            process_stopped
            and (sampler is None or not sampler.is_alive())
            and (ingestion is None or not ingestion.is_alive())
        )
        cleanup_owned_resources(
            base_dsn=base_dsn,
            schema=schema,
            runtime=runtime,
            credentials_path=credentials_path,
            output=output,
            activities_stopped=activities_stopped,
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--users", type=int, default=50)
    parser.add_argument("--spawn-rate", type=float, default=5)
    parser.add_argument("--warmup", type=int, default=15)
    parser.add_argument("--duration", type=int, default=75)
    parser.add_argument("--port", type=int)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--transport", choices=("rest", "mcp"), default="rest")
    args = parser.parse_args()
    if args.users < 2 or args.users > 200 or args.spawn_rate <= 0:
        parser.error("users must be 2..200 and spawn-rate must be positive")
    if args.warmup < 0 or args.duration <= 0:
        parser.error("warmup must be non-negative and duration positive")
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())

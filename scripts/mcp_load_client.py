"""Official MCP client load driver, using the same frozen corpus and result checks."""

import asyncio
import hashlib
import json
import math
import os
import sys
import time
from pathlib import Path

import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests" / "load"))
from harness import CaseMetrics, evaluate_read, evaluate_search, load_and_validate


def decode_result(result):
    payload = result.structured_content
    if payload is None:
        try:
            text = next(part.text for part in result.content if hasattr(part, "text"))
            # SDK 2.2 wraps deliberate ToolError text with this documented tool prefix.
            if result.is_error:
                for tool in ("kb_search", "kb_read"):
                    text = text.removeprefix(f"Error executing tool {tool}: ")
            payload = json.loads(text)
        except (ValueError, StopIteration):
            payload = {}
    # These are BUSINESS adapter statuses for shared validation, not wire HTTP status.
    if result.is_error:
        code = payload.get("error", {}).get("code") if isinstance(payload, dict) else None
        return (404 if code == "NOT_FOUND" else 500), payload
    return 200, payload


def select_read_version(case, payload):
    expected = case["read"]["version_id"]
    if case["search"]["expect"] != "hit" or not isinstance(payload, dict):
        return expected
    items = payload.get("items", [])
    for item in items[: case["search"].get("max_rank", 1)]:
        if str(item.get("document_id")) == str(case["search"]["document_id"]) and str(
            item.get("version_id")
        ) == str(expected):
            return expected
    return items[0].get("version_id", expected) if items else expected


def summarize_cycles(rows, duration):
    latencies = sorted(value for row in rows for value in row["latencies"])
    result = {
        "cycles": len(rows),
        "requests": len(latencies),
        "correct_cycles": sum(row["search_ok"] and row["read_ok"] for row in rows),
        "vector_degraded_cycles": sum(row["degraded"] for row in rows),
        "average_requests_per_second": round(len(latencies) / duration, 3),
    }
    for percentile in (50, 95, 99):
        result[f"p{percentile}_ms"] = (
            round(latencies[math.ceil(len(latencies) * percentile / 100) - 1], 3)
            if latencies
            else None
        )
    return result


async def measure(profile, credentials):
    """Each initialized client keeps exactly one tool request in flight."""
    rows, failures, initialized = [], [], set()
    cases_metrics = CaseMetrics()
    start = asyncio.Event()
    clock = {}

    async def worker(index, credential):
        await asyncio.sleep(index / profile["spawn_rate"])
        case_ids = set(credential["case_ids"])
        cases = [case for case in profile["cases"] if case["id"] in case_ids]
        if not cases:
            raise ValueError("Credential has no applicable cases")
        async with (
            httpx2.AsyncClient(
                headers={"Authorization": "Bearer " + credential["token"]},
                follow_redirects=False,
                trust_env=False,
                timeout=30,
            ) as http,
            Client(
                streamable_http_client(profile["target"]["base_url"] + "/mcp", http_client=http),
                mode="legacy",
            ) as agent,
        ):
            initialized.add(index)
            await start.wait()
            count = index
            while time.monotonic() < clock["end"]:
                case = cases[count % len(cases)]
                count += 1
                began = time.monotonic()
                args = {
                    "query": case["query"],
                    "mode": case["mode"],
                    "limit": 5,
                    **case.get("filters", {}),
                }
                response = await agent.call_tool("kb_search", args, read_timeout_seconds=30)
                searched = time.monotonic()
                status, payload = decode_result(response)
                search = evaluate_search(case, status, payload)
                response = await agent.call_tool(
                    "kb_read",
                    {
                        "version_id": select_read_version(case, payload),
                        "limit": 5,
                        "max_chars": 12000,
                    },
                    read_timeout_seconds=30,
                )
                ended = time.monotonic()
                status, payload = decode_result(response)
                read = evaluate_read(case, status, payload)
                # Exclude warmup and cycles crossing either measured boundary.
                if began >= clock["measured"] and ended <= clock["end"]:
                    rows.append(
                        {
                            "search_ok": search.business_ok,
                            "read_ok": read.business_ok,
                            "degraded": search.vector_degraded,
                            "latencies": [
                                (searched - began) * 1000,
                                (ended - searched) * 1000,
                            ],
                        }
                    )
                    cases_metrics.record(
                        case["id"],
                        "search",
                        business_ok=search.business_ok,
                        vector_degraded=search.vector_degraded,
                    )
                    cases_metrics.record(
                        case["id"],
                        "read",
                        business_ok=read.business_ok,
                        vector_degraded=False,
                    )

    async def guarded(index, credential):
        try:
            await worker(index, credential)
        except Exception as exc:  # noqa: BLE001 - redact client exceptions at the probe boundary.
            # Never persist exception text: it may contain request headers or private content.
            failures.append({"client": index, "error_type": type(exc).__name__})

    tasks = [
        asyncio.create_task(guarded(i, credential))
        for i, credential in enumerate(credentials[: profile["users"]])
    ]
    setup_started = time.monotonic()
    try:
        deadline = setup_started + profile["users"] / profile["spawn_rate"] + 30
        while len(initialized) < profile["users"]:
            if failures or time.monotonic() >= deadline:
                raise RuntimeError("MCP clients could not all initialize")
            await asyncio.sleep(0.02)
        clock["measured"] = time.monotonic() + profile["warmup_seconds"]
        clock["end"] = clock["measured"] + profile["duration_after_warmup_seconds"]
        setup_seconds = time.monotonic() - setup_started
        start.set()
        await asyncio.wait_for(
            asyncio.gather(*tasks),
            profile["warmup_seconds"] + profile["duration_after_warmup_seconds"] + 40,
        )
        return {
            "schema_version": 1,
            "driver": "official_mcp_sdk",
            "initialized_clients": len(initialized),
            "setup_seconds": round(setup_seconds, 3),
            "transport_failures": failures,
            "measured_elapsed_seconds": profile["duration_after_warmup_seconds"],
            "complete_cycle_quality_by_case": cases_metrics.snapshot(),
            "mcp": summarize_cycles(rows, profile["duration_after_warmup_seconds"]),
            "scope": "Independent official MCP clients; no model generation or Agent reasoning",
        }
    except (RuntimeError, TimeoutError) as exc:
        failures.append({"phase": "driver", "error_type": type(exc).__name__})
        return {
            "schema_version": 1,
            "driver": "official_mcp_sdk",
            "initialized_clients": len(initialized),
            "transport_failures": failures,
            "measured_elapsed_seconds": 0,
            "complete_cycle_quality_by_case": cases_metrics.snapshot(),
            "mcp": summarize_cycles(rows, profile["duration_after_warmup_seconds"]),
            "scope": "Failed MCP probe; not acceptance evidence",
        }
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


def main():
    profile_path = Path(os.environ["AGICO_KB_LOAD_PROFILE"])
    digest = hashlib.sha256(profile_path.read_bytes()).hexdigest()
    if digest != os.environ["AGICO_KB_LOAD_PROFILE_SHA256"]:
        raise ValueError("Frozen profile mismatch")
    supplied = json.loads(profile_path.read_text(encoding="utf-8"))
    profile, credentials = load_and_validate(
        profile_path, os.environ["AGICO_KB_LOAD_CREDENTIALS"], supplied["target"]["base_url"]
    )
    if profile["workload"]["transport"] != "mcp_http":
        raise ValueError("MCP driver requires an MCP profile")
    result = asyncio.run(measure(profile, credentials))
    result["profile_sha256"] = digest
    Path(os.environ["AGICO_KB_LOAD_METRICS_PATH"]).write_text(
        json.dumps(result, indent=2), encoding="utf-8"
    )
    return int(
        bool(result["transport_failures"])
        or not result["mcp"]["cycles"]
        or result["mcp"]["correct_cycles"] != result["mcp"]["cycles"]
    )


if __name__ == "__main__":
    raise SystemExit(main())

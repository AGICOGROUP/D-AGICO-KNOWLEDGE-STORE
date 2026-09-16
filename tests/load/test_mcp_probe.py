import asyncio
import json
from types import SimpleNamespace

from scripts.mcp_load_client import decode_result, measure, select_read_version, summarize_cycles


def test_mcp_tool_error_preserves_hidden_resource_contract_without_claiming_http_404():
    result = SimpleNamespace(
        structured_content=None,
        is_error=True,
        content=[SimpleNamespace(text='{"error":{"code":"NOT_FOUND"}}')],
    )
    status, payload = decode_result(result)
    assert status == 404  # Business adapter status; MCP HTTP remains a separate transport.
    assert payload["error"]["code"] == "NOT_FOUND"


def test_official_sdk_tool_error_prefix_preserves_expected_denial():
    result = SimpleNamespace(
        structured_content=None,
        is_error=True,
        content=[
            SimpleNamespace(text='Error executing tool kb_read: {"error":{"code":"NOT_FOUND"}}')
        ],
    )
    assert decode_result(result) == (404, {"error": {"code": "NOT_FOUND"}})


def test_read_uses_expected_source_in_top_five_not_unrelated_first_result():
    case = {
        "search": {"expect": "hit", "document_id": "wanted", "max_rank": 5},
        "read": {"version_id": "correct"},
    }
    result = {
        "items": [
            {"document_id": "other", "version_id": "wrong"},
            {"document_id": "wanted", "version_id": "correct"},
        ]
    }
    assert select_read_version(case, result) == "correct"


def test_summary_requires_both_search_and_read_and_reports_real_degradation():
    rows = [
        {"search_ok": True, "read_ok": True, "degraded": False, "latencies": [10, 20]},
        {"search_ok": False, "read_ok": True, "degraded": True, "latencies": [30, 40]},
        {"search_ok": True, "read_ok": False, "degraded": False, "latencies": [50, 60]},
    ]
    result = summarize_cycles(rows, 10)
    assert result["cycles"] == 3
    assert result["correct_cycles"] == 1
    assert result["requests"] == 6
    assert result["vector_degraded_cycles"] == 1
    assert result["average_requests_per_second"] == 0.6


def test_client_setup_failure_emits_failed_redacted_evidence(monkeypatch):
    def broken(**kwargs):
        raise RuntimeError("private-bearer-token")

    monkeypatch.setattr("scripts.mcp_load_client.httpx2.AsyncClient", broken)
    profile = {
        "users": 1,
        "spawn_rate": 10,
        "duration_after_warmup_seconds": 1,
        "cases": [{"id": "case"}],
    }
    result = asyncio.run(
        measure(profile, [{"case_ids": ["case"], "token": "private-bearer-token"}])
    )
    assert result["initialized_clients"] == 0
    assert result["transport_failures"]
    assert result["mcp"]["cycles"] == 0
    assert "private-bearer-token" not in json.dumps(result)

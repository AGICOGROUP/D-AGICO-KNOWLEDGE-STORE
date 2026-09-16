from decimal import Decimal
from threading import Event

import pytest
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from scripts.run_load_probe import (
    _cases,
    _delayed_ingestion,
    cleanup_owned_resources,
    corpus_counts,
    resource_summary,
    validate_runtime_path,
)


def test_runtime_cleanup_target_must_be_direct_child_of_runtime_root(tmp_path):
    root = tmp_path / "runtime"
    child = root / "load_abc"
    child.mkdir(parents=True)

    assert validate_runtime_path(root, child) == child.resolve()
    with pytest.raises(ValueError):
        validate_runtime_path(root, root)
    with pytest.raises(ValueError):
        validate_runtime_path(root, tmp_path)


def test_resource_summary_reports_observed_peaks_without_inventing_queue_metrics():
    samples = [
        {
            "system_cpu_percent": 20.0,
            "system_memory_percent": 40.0,
            "server_cpu_percent": 90.0,
            "server_rss_bytes": 100,
            "db_connections": 3,
            "db_active": 2,
            "db_waiting": 1,
            "queued_jobs": 4,
        },
        {
            "system_cpu_percent": 30.0,
            "system_memory_percent": 50.0,
            "server_cpu_percent": 110.0,
            "server_rss_bytes": 200,
            "db_connections": 5,
            "db_active": 4,
            "db_waiting": 2,
            "queued_jobs": 1,
        },
    ]

    assert resource_summary(samples) == {
        "samples": 2,
        "system_cpu_peak_percent": 30.0,
        "system_memory_peak_percent": 50.0,
        "server_cpu_peak_percent": 110.0,
        "server_rss_peak_bytes": 200,
        "db_connections_peak": 5,
        "db_active_peak": 4,
        "db_waiting_peak": 2,
        "queued_jobs_peak": 4,
        "db_pool_waiters": "not exposed by the current server",
        "embedding_queue_depth": "not exposed by the current server",
    }


def test_corpus_counts_normalize_postgres_numeric_sum_for_frozen_json():
    assert corpus_counts((104, 103, 106, 104, Decimal(12345))) == {
        "documents": 104,
        "active_documents": 103,
        "versions": 106,
        "chunks": 104,
        "bytes": 12345,
    }


def test_semantic_probe_accepts_observed_top_five_and_unknown_avoids_corpus_terms():
    records = [
        {
            "key": f"{organization}-{index}",
            "organization_id": organization,
            "document_id": f"doc-{organization}-{index}",
            "version_id": f"version-{organization}-{index}",
        }
        for organization in ("baiste", "xingyuan")
        for index in range(10)
    ]

    cases, _ = _cases(records)
    semantic = next(case for case in cases if case["id"] == "baiste-semantic")
    unknown = next(case for case in cases if case["id"] == "baiste-unknown")

    assert semantic["search"]["max_rank"] == 5
    assert unknown["query"] == "量子纠缠中继器"


def test_delayed_ingestion_can_be_cancelled_before_it_touches_resources():
    stop = Event()
    stop.set()
    calls = []
    output = {}

    _delayed_ingestion(stop, 60, lambda: calls.append("called"), output)

    assert calls == []
    assert output == {"completed": False, "thread_status": "cancelled_before_start"}


def test_database_cleanup_failure_is_bounded_redacted_and_does_not_keep_credentials(tmp_path):
    runtime_root = tmp_path / "runtime"
    runtime = runtime_root / "load_owned"
    runtime.mkdir(parents=True)
    credentials = runtime / "credentials.json"
    credentials.write_text("secret-token", encoding="utf-8")
    output = tmp_path / "result"
    output.mkdir()
    observed = {}

    def unavailable(dsn, **kwargs):
        observed.update(conninfo_to_dict(dsn))
        raise RuntimeError("password=do-not-report")

    removed = cleanup_owned_resources(
        base_dsn=make_conninfo(
            host="127.0.0.1",
            port=15432,
            dbname="agico_test",
            user="agico_dev",
            password="local-secret",
        ),
        schema="load_owned",
        runtime=runtime,
        credentials_path=credentials,
        output=output,
        activities_stopped=True,
        connect=unavailable,
        runtime_root=runtime_root,
    )

    marker = (output / "cleanup-required.json").read_text(encoding="utf-8")
    assert removed is False
    assert not runtime.exists()
    assert observed["connect_timeout"] == "3"
    assert "lock_timeout=3000" in observed["options"]
    assert "statement_timeout=10000" in observed["options"]
    assert '"schema": "load_owned"' in marker
    assert "do-not-report" not in marker and "local-secret" not in marker


def test_active_owned_worker_retains_runtime_but_removes_credential_file(tmp_path):
    runtime_root = tmp_path / "runtime"
    runtime = runtime_root / "load_owned"
    runtime.mkdir(parents=True)
    credentials = runtime / "credentials.json"
    credentials.write_text("secret-token", encoding="utf-8")
    output = tmp_path / "result"
    output.mkdir()

    removed = cleanup_owned_resources(
        base_dsn="unused",
        schema="load_owned",
        runtime=runtime,
        credentials_path=credentials,
        output=output,
        activities_stopped=False,
        runtime_root=runtime_root,
    )

    assert removed is False
    assert runtime.exists()
    assert not credentials.exists()
    assert "owned activity did not stop" in (output / "cleanup-required.json").read_text(
        encoding="utf-8"
    )

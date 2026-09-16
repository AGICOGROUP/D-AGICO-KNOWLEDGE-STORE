import json

import pytest
from harness import (
    CaseMetrics,
    CredentialPool,
    Metrics,
    ProfileError,
    UserCohort,
    evaluate_read,
    evaluate_search,
    formal_acceptance_blockers,
    freeze_profile,
    load_and_validate,
)


def profile(**overrides):
    value = {
        "schema_version": 1,
        "profile_kind": "development",
        "users": 2,
        "spawn_rate": 1,
        "warmup_seconds": 2,
        "duration_after_warmup_seconds": 5,
        "wait_time_seconds": 0,
        "target": {"base_url": "http://127.0.0.1:18080"},
        "slo": {
            "p50_ms": None,
            "p95_ms": None,
            "p99_ms": None,
            "http_success_rate": None,
            "business_correctness_rate": None,
            "max_vector_degraded_rate": None,
        },
        "cases": [
            {
                "id": "semantic",
                "mode": "content",
                "query": "怎样避免高速机构过热磨损",
                "semantic_only": True,
                "search": {
                    "expect": "hit",
                    "document_id": "doc-1",
                    "version_id": "version-1",
                    "chunk_contains": "持续注入冷却剂",
                    "max_rank": 1,
                },
                "read": {
                    "expect": "content",
                    "version_id": "version-1",
                    "source_version": "2026-A",
                    "contains": "持续注入冷却剂",
                },
            }
        ],
    }
    value.update(overrides)
    return value


def test_profile_rejects_duplicate_or_insufficient_credentials(tmp_path):
    profile_path = tmp_path / "profile.json"
    credential_path = tmp_path / "credentials.json"
    profile_path.write_text(json.dumps(profile()), encoding="utf-8")
    credential_path.write_text(json.dumps({"tokens": ["same", "same"]}), encoding="utf-8")

    with pytest.raises(ProfileError, match="unique credentials"):
        load_and_validate(profile_path, credential_path, "http://127.0.0.1:18080")


def test_profile_rejects_credential_whose_cases_do_not_exist(tmp_path):
    profile_path = tmp_path / "profile.json"
    credential_path = tmp_path / "credentials.json"
    profile_path.write_text(json.dumps(profile()), encoding="utf-8")
    credential_path.write_text(
        json.dumps(
            {
                "tokens": [
                    {"token": "one", "case_ids": ["unknown"]},
                    {"token": "two", "case_ids": ["semantic"]},
                ]
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ProfileError, match="case"):
        load_and_validate(profile_path, credential_path, "http://127.0.0.1:18080")


def test_user_cohort_records_departure_during_measurement_but_not_normal_shutdown():
    cohort = UserCohort()
    cohort.entered()
    cohort.entered()
    cohort.begin()
    cohort.left()
    assert cohort.snapshot() == {"started": 2, "minimum_live_while_measuring": 1}
    cohort.end()
    cohort.left()
    assert cohort.snapshot()["minimum_live_while_measuring"] == 1


def test_profile_requires_warmup_to_outlast_user_spawn(tmp_path):
    profile_path = tmp_path / "profile.json"
    credential_path = tmp_path / "credentials.json"
    profile_path.write_text(
        json.dumps(profile(users=2, spawn_rate=1, warmup_seconds=1)), encoding="utf-8"
    )
    credential_path.write_text(json.dumps({"tokens": ["one", "two"]}), encoding="utf-8")

    with pytest.raises(ProfileError, match="Warmup must include the complete ramp"):
        load_and_validate(profile_path, credential_path, "http://127.0.0.1:18080")


def test_unconfirmed_acceptance_inputs_are_explicit_blockers():
    candidate = profile(
        profile_kind="acceptance",
        status="blocked_pending_business_inputs",
        users=50,
        spawn_rate=5,
        warmup_seconds=120,
        duration_after_warmup_seconds=1800,
        target={"base_url": None, "server_description": None, "client_version": None},
        corpus={"kind": "approved_company_corpus", "documents": None},
    )

    blockers = formal_acceptance_blockers(candidate)

    assert "profile status is not frozen" in blockers
    assert "target.base_url is unconfirmed" in blockers
    assert "slo.p95_ms is unconfirmed" in blockers
    assert "business corpus metadata is incomplete" in blockers


def test_unconfirmed_acceptance_profile_cannot_run(tmp_path):
    candidate = profile(
        profile_kind="acceptance",
        status="blocked_pending_business_inputs",
        users=50,
        spawn_rate=5,
        warmup_seconds=120,
        duration_after_warmup_seconds=1800,
        target={
            "base_url": "https://load.example.test",
            "server_description": None,
            "client_version": None,
        },
    )
    profile_path = tmp_path / "profile.json"
    credential_path = tmp_path / "credentials.json"
    profile_path.write_text(json.dumps(candidate), encoding="utf-8")
    credential_path.write_text(
        json.dumps({"tokens": [f"token-{index}" for index in range(50)]}), encoding="utf-8"
    )

    with pytest.raises(ProfileError, match="Formal acceptance is blocked"):
        load_and_validate(profile_path, credential_path, "https://load.example.test")


@pytest.mark.parametrize(
    "target,host",
    [
        ("https://example.invalid", "https://other.invalid"),
        ("http://127.0.0.1:18080/path", "http://127.0.0.1:18080"),
        ("file:///tmp/socket", "file:///tmp/socket"),
    ],
)
def test_profile_rejects_host_mismatch_path_and_non_http(tmp_path, target, host):
    profile_path = tmp_path / "profile.json"
    credential_path = tmp_path / "credentials.json"
    profile_path.write_text(json.dumps(profile(target={"base_url": target})), encoding="utf-8")
    credential_path.write_text(json.dumps({"tokens": ["one", "two"]}), encoding="utf-8")

    with pytest.raises(ProfileError):
        load_and_validate(profile_path, credential_path, host)


def test_profile_hash_must_match_frozen_copy(tmp_path):
    source = tmp_path / "profile.json"
    frozen = tmp_path / "frozen.json"
    source.write_text(json.dumps(profile()), encoding="utf-8")
    digest = freeze_profile(source, frozen)
    source.write_text("{}", encoding="utf-8")

    assert digest == freeze_profile(frozen, tmp_path / "second.json")
    assert json.loads(frozen.read_text(encoding="utf-8"))["users"] == 2


def test_http_200_wrong_hit_is_business_failure_and_degradation_is_separate():
    case = profile()["cases"][0]
    result = evaluate_search(
        case,
        200,
        {
            "items": [
                {
                    "document_id": "wrong-doc",
                    "version_id": "wrong-version",
                    "chunk_id": "wrong-chunk",
                    "excerpt": "unrelated",
                }
            ],
            "warnings": ["查询向量暂不可用，本次仅使用关键词检索。"],
        },
    )

    assert result.http_ok is True
    assert result.business_ok is False
    assert result.vector_degraded is True
    assert result.reason == "expected source is not within allowed rank"


def test_semantic_only_hit_does_not_pass_when_vector_search_degraded():
    case = profile()["cases"][0]
    result = evaluate_search(
        case,
        200,
        {
            "items": [
                {
                    "document_id": "doc-1",
                    "version_id": "version-1",
                    "chunk_id": "chunk-1",
                    "excerpt": "持续注入冷却剂",
                }
            ],
            "warnings": ["查询向量暂不可用，本次仅使用关键词检索。"],
        },
    )

    assert result.business_ok is False
    assert result.reason == "semantic-only query used degraded retrieval"


def test_no_answer_and_cross_identity_leak_are_checked():
    case = {
        "search": {"expect": "empty", "forbidden_document_ids": ["private-doc"]},
        "semantic_only": False,
    }
    empty = evaluate_search(case, 200, {"items": [], "warnings": []})
    leak = evaluate_search(
        case,
        200,
        {"items": [{"document_id": "private-doc", "version_id": "v"}], "warnings": []},
    )

    assert empty.business_ok is True
    assert leak.business_ok is False
    assert leak.reason == "forbidden source returned"


def test_absent_expectation_allows_unrelated_authorized_hits_but_rejects_private_source():
    case = {
        "search": {"expect": "absent", "forbidden_document_ids": ["private-doc"]},
        "semantic_only": False,
    }

    unrelated = evaluate_search(
        case,
        200,
        {"items": [{"document_id": "public-doc", "version_id": "v"}], "warnings": []},
    )
    leak = evaluate_search(
        case,
        200,
        {"items": [{"document_id": "private-doc", "version_id": "v"}], "warnings": []},
    )

    assert unrelated.business_ok is True
    assert leak.business_ok is False


def test_read_requires_expected_version_source_and_content():
    expected = {
        "read": {
            "expect": "content",
            "version_id": "version-1",
            "source_version": "2026-A",
            "contains": "持续注入冷却剂",
        }
    }
    wrong_source = evaluate_read(
        expected,
        200,
        {
            "version_id": "version-1",
            "source_version": "2025-Z",
            "items": [{"text": "持续注入冷却剂"}],
        },
    )
    denied = evaluate_read({"read": {"expect": "denied"}}, 404, {"error": {"code": "NOT_FOUND"}})

    assert wrong_source.business_ok is False
    assert wrong_source.reason == "wrong source revision"
    assert denied.http_ok is False and denied.business_ok is True


def test_each_credential_is_claimed_once():
    pool = CredentialPool(["a", "b"])

    assert {pool.claim(), pool.claim()} == {"a", "b"}
    with pytest.raises(RuntimeError, match="No unused credential"):
        pool.claim()


def test_metrics_keep_transport_correctness_and_degradation_independent():
    metrics = Metrics()
    metrics.record("search", http_ok=True, business_ok=False, vector_degraded=True)
    metrics.record("read", http_ok=True, business_ok=True, vector_degraded=False)

    assert metrics.snapshot() == {
        "search": {"requests": 1, "http_ok": 1, "business_ok": 0, "vector_degraded": 1},
        "read": {"requests": 1, "http_ok": 1, "business_ok": 1, "vector_degraded": 0},
    }


def test_case_metrics_explain_which_business_probe_failed():
    metrics = CaseMetrics()
    metrics.record("semantic", "search", business_ok=False, vector_degraded=True)
    metrics.record("semantic", "read", business_ok=True, vector_degraded=False)

    assert metrics.snapshot() == {
        "semantic": {
            "search_requests": 1,
            "search_business_ok": 0,
            "read_requests": 1,
            "read_business_ok": 1,
            "vector_degraded": 1,
        }
    }

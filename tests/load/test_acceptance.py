from copy import deepcopy

from harness import assess_acceptance


def inputs():
    profile = {
        "profile_kind": "acceptance",
        "status": "frozen_for_acceptance",
        "users": 50,
        "duration_after_warmup_seconds": 1800,
        "target": {
            "base_url": "https://kb.example",
            "server_description": "confirmed",
            "client_version": "fixed",
        },
        "corpus": {
            "documents": 100,
            "bytes": 10000,
            "chunks": 100,
            "embedding_model_identity": "pinned",
        },
        "business_signoff": {"approved": True, "reviewer": "test-owner"},
        "cases": [
            {"id": "allowed", "read": {"expect": "content"}},
            {"id": "private", "read": {"expect": "denied"}},
        ],
        "slo": {
            "p50_ms": 100,
            "p95_ms": 200,
            "p99_ms": 300,
            "http_success_rate": 1.0,
            "business_correctness_rate": 1.0,
            "max_vector_degraded_rate": 0.0,
        },
    }
    metrics = {
        "measured_elapsed_seconds": 1800,
        "expected_denial_responses": 10,
        "users": {"started": 50, "minimum_live_while_measuring": 50},
        "locust": {"requests": 40, "p50_ms": 50, "p95_ms": 100, "p99_ms": 150},
        "quality": {
            "search": {"requests": 20, "http_ok": 20},
            "read": {"requests": 20, "http_ok": 10},
        },
        "complete_cycle_quality_by_case": {
            case: {
                "search_requests": 10,
                "read_requests": 10,
                "search_business_ok": 10,
                "read_business_ok": 10,
                "vector_degraded": 0,
            }
            for case in ("allowed", "private")
        },
    }
    return profile, metrics


def test_frozen_thresholds_are_enforced_and_expected_denials_do_not_fake_http_failure():
    profile, metrics = inputs()
    assert assess_acceptance(profile, metrics)["status"] == "passed"
    metrics["locust"]["p95_ms"] = 250
    assert assess_acceptance(profile, metrics)["status"] == "failed"


def test_missing_samples_short_window_degradation_and_business_errors_block_pass():
    profile, baseline = inputs()
    for change in ("short", "empty", "degraded", "wrong_source"):
        metrics = deepcopy(baseline)
        if change == "short":
            metrics["measured_elapsed_seconds"] = 75
        if change == "empty":
            metrics["complete_cycle_quality_by_case"] = {}
        if change == "degraded":
            metrics["complete_cycle_quality_by_case"]["allowed"]["vector_degraded"] = 1
        if change == "wrong_source":
            metrics["complete_cycle_quality_by_case"]["allowed"]["read_business_ok"] = 9
        assert assess_acceptance(profile, metrics)["status"] == "failed", change


def test_unapproved_or_nonfinite_slo_cannot_pass_even_with_good_measurements():
    profile, metrics = inputs()
    profile["business_signoff"]["approved"] = False
    assert assess_acceptance(profile, metrics)["status"] == "blocked"
    profile["business_signoff"]["approved"] = True
    profile["slo"]["p95_ms"] = float("nan")
    assert assess_acceptance(profile, metrics)["status"] == "blocked"


def test_lost_user_cannot_pass_fifty_user_acceptance():
    profile, metrics = inputs()
    metrics["users"]["minimum_live_while_measuring"] = 49
    assert assess_acceptance(profile, metrics)["status"] == "failed"


def test_single_permission_failure_cannot_be_diluted_by_general_success_threshold():
    profile, metrics = inputs()
    profile["slo"]["business_correctness_rate"] = 0.95
    metrics["complete_cycle_quality_by_case"]["private"]["read_business_ok"] = 9
    verdict = assess_acceptance(profile, metrics)
    assert verdict["status"] == "failed"
    assert any("security" in reason for reason in verdict["reasons"])

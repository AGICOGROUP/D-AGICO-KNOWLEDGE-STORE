"""Closed-loop REST backend load probe with business-result validation."""

import hashlib
import json
import os
import random
import sys
import time
from itertools import cycle
from pathlib import Path

import gevent
from locust import HttpUser, events, task

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import (
    CaseMetrics,
    CredentialPool,
    Metrics,
    ProfileError,
    UserCohort,
    assess_acceptance,
    evaluate_read,
    evaluate_search,
    load_and_validate,
)

PROFILE = None
CREDENTIALS = None
PROFILE_SHA256 = None
METRICS = Metrics()
CASE_METRICS = CaseMetrics()
MEASURED_STARTED = None
EXPECTED_DENIALS = 0
COHORT = UserCohort()


def _json(response):
    try:
        return response.json()
    except ValueError:
        return None


def _reset_after_warmup(environment, seconds):
    global MEASURED_STARTED, EXPECTED_DENIALS
    gevent.sleep(seconds)
    environment.stats.reset_all()
    METRICS.reset()
    CASE_METRICS.reset()
    EXPECTED_DENIALS = 0
    MEASURED_STARTED = time.time()
    COHORT.begin()


@events.init.add_listener
def configure(environment, **_kwargs):
    global PROFILE, CREDENTIALS, PROFILE_SHA256
    profile_path = os.environ.get("AGICO_KB_LOAD_PROFILE")
    credential_path = os.environ.get("AGICO_KB_LOAD_CREDENTIALS")
    if not profile_path or not credential_path:
        raise ProfileError("AGICO_KB_LOAD_PROFILE and AGICO_KB_LOAD_CREDENTIALS are required")
    expected_hash = os.environ.get("AGICO_KB_LOAD_PROFILE_SHA256")
    profile_bytes = Path(profile_path).read_bytes()
    PROFILE_SHA256 = hashlib.sha256(profile_bytes).hexdigest()
    if not expected_hash or expected_hash != PROFILE_SHA256:
        raise ProfileError("Frozen profile hash is missing or does not match")
    host = environment.parsed_options.host
    PROFILE, credential_records = load_and_validate(profile_path, credential_path, host)
    if environment.parsed_options.num_users != PROFILE["users"]:
        raise ProfileError("Locust --users must equal the frozen profile")
    if environment.parsed_options.spawn_rate != PROFILE["spawn_rate"]:
        raise ProfileError("Locust --spawn-rate must equal the frozen profile")
    expected_seconds = PROFILE["warmup_seconds"] + PROFILE["duration_after_warmup_seconds"]
    if environment.parsed_options.run_time != expected_seconds:
        raise ProfileError("Locust --run-time must equal frozen warmup plus measured duration")
    CREDENTIALS = CredentialPool(credential_records)


@events.test_start.add_listener
def start_measurement(environment, **_kwargs):
    global MEASURED_STARTED
    warmup = PROFILE["warmup_seconds"]
    if warmup:
        gevent.spawn(_reset_after_warmup, environment, warmup)
    else:
        MEASURED_STARTED = time.time()
        COHORT.begin()


@events.test_stopping.add_listener
def stop_counting_users(environment, **_kwargs):
    COHORT.end()


@events.test_stop.add_listener
def write_quality_metrics(environment, **_kwargs):
    target = os.environ.get("AGICO_KB_LOAD_METRICS_PATH")
    if not target:
        return
    now = time.time()
    output = {
        "schema_version": 1,
        "profile_sha256": PROFILE_SHA256,
        "measured_started_at_unix": MEASURED_STARTED,
        "measured_elapsed_seconds": round(now - MEASURED_STARTED, 3) if MEASURED_STARTED else 0,
        "quality": METRICS.snapshot(),
        "complete_cycle_quality_by_case": CASE_METRICS.snapshot(),
        "expected_denial_responses": EXPECTED_DENIALS,
        "users": COHORT.snapshot(),
        "locust": {
            "requests": environment.stats.total.num_requests,
            "failures": environment.stats.total.num_failures,
            "average_requests_per_second": round(
                environment.stats.total.num_requests / max(now - MEASURED_STARTED, 0.001), 3
            )
            if MEASURED_STARTED
            else 0,
            "current_requests_per_second_at_stop": environment.stats.total.current_rps,
            "p50_ms": environment.stats.total.get_response_time_percentile(0.50),
            "p95_ms": environment.stats.total.get_response_time_percentile(0.95),
            "p99_ms": environment.stats.total.get_response_time_percentile(0.99),
        },
    }
    output["acceptance"] = assess_acceptance(PROFILE, output)
    if PROFILE["profile_kind"] == "acceptance" and output["acceptance"]["status"] != "passed":
        environment.process_exit_code = 1
    path = Path(target)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


class KnowledgeStoreUser(HttpUser):
    def wait_time(self):
        return 0

    def on_start(self):
        record = CREDENTIALS.claim()
        if isinstance(record, str):
            token = record
            allowed = list(PROFILE["cases"])
        else:
            token = record["token"]
            selected = set(record.get("case_ids", []))
            allowed = [case for case in PROFILE["cases"] if not selected or case["id"] in selected]
        if not allowed:
            raise RuntimeError("Credential has no applicable cases")
        self.headers = {"Authorization": "Bearer " + token}
        # Keep the mix varied without adding fake query terms.
        random.Random(str(record)).shuffle(allowed)
        self.cases = cycle(allowed)
        COHORT.entered()
        self._cohort_registered = True

    def on_stop(self):
        if getattr(self, "_cohort_registered", False):
            COHORT.left()

    @task
    def search_then_read(self):
        global EXPECTED_DENIALS
        measured_cycle = MEASURED_STARTED is not None
        case = next(self.cases)
        body = {"query": case["query"], "mode": case["mode"], "limit": 5}
        body.update(case.get("filters", {}))
        search_payload = None
        search_result = None
        with self.client.post(
            "/v1/search",
            headers=self.headers,
            json=body,
            name=f"/v1/search [{case['mode']}]",
            catch_response=True,
            allow_redirects=False,
            timeout=30,
        ) as response:
            search_payload = _json(response)
            search_result = evaluate_search(case, response.status_code, search_payload)
            METRICS.record(
                "search",
                http_ok=search_result.http_ok,
                business_ok=search_result.business_ok,
                vector_degraded=search_result.vector_degraded,
            )
            if not search_result.business_ok:
                response.failure(search_result.reason or "search business validation failed")

        expected_read = case["read"]["version_id"]
        read_version = expected_read
        if case["search"]["expect"] == "hit" and isinstance(search_payload, dict):
            items = search_payload.get("items", [])
            max_rank = case["search"].get("max_rank", 1)
            match = next(
                (
                    item
                    for item in items[:max_rank]
                    if isinstance(item, dict)
                    and str(item.get("document_id")) == str(case["search"].get("document_id"))
                    and str(item.get("version_id")) == str(expected_read)
                ),
                None,
            )
            if match:
                read_version = match.get("version_id", expected_read)
            elif items and isinstance(items[0], dict):
                read_version = items[0].get("version_id", expected_read)
        with self.client.post(
            "/v1/read",
            headers=self.headers,
            json={"version_id": read_version, "limit": 5, "max_chars": 12000},
            name="/v1/read",
            catch_response=True,
            allow_redirects=False,
            timeout=30,
        ) as response:
            read_result = evaluate_read(case, response.status_code, _json(response))
            if case["read"]["expect"] == "denied" and read_result.business_ok:
                EXPECTED_DENIALS += 1
            METRICS.record(
                "read",
                http_ok=read_result.http_ok,
                business_ok=read_result.business_ok,
                vector_degraded=False,
            )
            if read_result.business_ok:
                # Expected permission concealment is a correct business result even though it is 404.
                response.success()
            else:
                response.failure(read_result.reason or "read business validation failed")
        if measured_cycle:
            CASE_METRICS.record(
                case["id"],
                "search",
                business_ok=search_result.business_ok,
                vector_degraded=search_result.vector_degraded,
            )
            CASE_METRICS.record(
                case["id"],
                "read",
                business_ok=read_result.business_ok,
                vector_degraded=False,
            )

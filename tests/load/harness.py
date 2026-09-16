"""Shared validation and accounting for the REST load probe."""

import hashlib
import json
import math
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from urllib.parse import urlsplit


class ProfileError(ValueError):
    """The frozen load profile cannot be executed safely."""


class CredentialPool:
    def __init__(self, tokens):
        self._tokens = list(tokens)
        self._lock = Lock()

    def claim(self):
        with self._lock:
            if not self._tokens:
                raise RuntimeError("No unused credential is available")
            return self._tokens.pop()


class UserCohort:
    """Record actual participants; freeze before Locust performs its normal shutdown."""

    def __init__(self):
        self.started = self.live = 0
        self.minimum = None
        self.measuring = False

    def entered(self):
        self.started += 1
        self.live += 1

    def left(self):
        self.live -= 1
        if self.measuring:
            self.minimum = min(self.minimum, self.live)

    def begin(self):
        self.minimum = self.live
        self.measuring = True

    def end(self):
        self.measuring = False

    def snapshot(self):
        return {"started": self.started, "minimum_live_while_measuring": self.minimum}


class Metrics:
    def __init__(self):
        self._values = defaultdict(
            lambda: {"requests": 0, "http_ok": 0, "business_ok": 0, "vector_degraded": 0}
        )
        self._lock = Lock()

    def record(self, operation, *, http_ok, business_ok, vector_degraded):
        with self._lock:
            row = self._values[operation]
            row["requests"] += 1
            row["http_ok"] += int(http_ok)
            row["business_ok"] += int(business_ok)
            row["vector_degraded"] += int(vector_degraded)

    def snapshot(self):
        with self._lock:
            return {key: dict(value) for key, value in self._values.items()}

    def reset(self):
        with self._lock:
            self._values.clear()


class CaseMetrics:
    def __init__(self):
        self._values = defaultdict(
            lambda: {
                "search_requests": 0,
                "search_business_ok": 0,
                "read_requests": 0,
                "read_business_ok": 0,
                "vector_degraded": 0,
            }
        )
        self._lock = Lock()

    def record(self, case_id, operation, *, business_ok, vector_degraded):
        with self._lock:
            row = self._values[case_id]
            row[f"{operation}_requests"] += 1
            row[f"{operation}_business_ok"] += int(business_ok)
            row["vector_degraded"] += int(vector_degraded)

    def snapshot(self):
        with self._lock:
            return {key: dict(value) for key, value in self._values.items()}

    def reset(self):
        with self._lock:
            self._values.clear()


@dataclass(frozen=True)
class Evaluation:
    http_ok: bool
    business_ok: bool
    vector_degraded: bool = False
    reason: str | None = None


def _load_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProfileError("Profile input is missing or invalid JSON") from exc


def _origin(value):
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ProfileError("Target must be an HTTP(S) origin without credentials or a path")
    return f"{parsed.scheme}://{parsed.hostname.lower()}" + (
        f":{parsed.port}" if parsed.port is not None else ""
    )


def _validate_case(case):
    if (
        not isinstance(case, dict)
        or not case.get("id")
        or case.get("mode")
        not in {
            "content",
            "files",
        }
    ):
        raise ProfileError("Each case needs an id and supported search mode")
    if not isinstance(case.get("query"), str):
        raise ProfileError("Each case needs a query")
    search = case.get("search")
    read = case.get("read")
    if not isinstance(search, dict) or search.get("expect") not in {"hit", "empty", "absent"}:
        raise ProfileError("Each case needs a supported search expectation")
    if not isinstance(read, dict) or read.get("expect") not in {"content", "denied"}:
        raise ProfileError("Each case needs a supported read expectation")
    if search["expect"] == "hit" and not (search.get("document_id") and search.get("version_id")):
        raise ProfileError("Hit cases must identify the expected document and version")
    if read["expect"] == "content" and not read.get("version_id"):
        raise ProfileError("Content reads must identify the expected version")


def formal_acceptance_blockers(profile):
    blockers = []
    if profile.get("profile_kind") != "acceptance":
        blockers.append("profile is not an acceptance profile")
    if profile.get("status") != "frozen_for_acceptance":
        blockers.append("profile status is not frozen")
    target = profile.get("target", {})
    for key in ("base_url", "server_description", "client_version"):
        if not target.get(key):
            blockers.append(f"target.{key} is unconfirmed")
    slo = profile.get("slo", {})
    for key in (
        "p50_ms",
        "p95_ms",
        "p99_ms",
        "http_success_rate",
        "business_correctness_rate",
        "max_vector_degraded_rate",
    ):
        value = slo.get(key)
        if (
            type(value) not in (int, float)
            or not math.isfinite(value)
            or (key.endswith("_ms") and value <= 0)
            or (not key.endswith("_ms") and not 0 <= value <= 1)
        ):
            blockers.append(f"slo.{key} is unconfirmed")
    corpus = profile.get("corpus", {})
    if any(
        corpus.get(key) in (None, "")
        for key in ("documents", "bytes", "chunks", "embedding_model_identity")
    ):
        blockers.append("business corpus metadata is incomplete")
    signoff = profile.get("business_signoff", {})
    if signoff.get("approved") is not True or not signoff.get("reviewer"):
        blockers.append("business owner sign-off is missing")
    return blockers


def assess_acceptance(profile, metrics):
    """Evaluate the frozen REST sub-gate; never claim whole-company acceptance."""
    blockers = formal_acceptance_blockers(profile)
    if blockers:
        return {"status": "blocked", "reasons": blockers}
    reasons = []
    users = metrics.get("users", {})
    if (
        users.get("started") != profile["users"]
        or users.get("minimum_live_while_measuring") != profile["users"]
    ):
        reasons.append("actual users did not sustain the frozen participant count")
    if metrics.get("measured_elapsed_seconds", 0) < profile["duration_after_warmup_seconds"] - 0.5:
        reasons.append("measured duration is shorter than the frozen profile")
    rows = metrics.get("complete_cycle_quality_by_case", {})
    for case in profile["cases"]:
        row = rows.get(case["id"], {})
        if case["read"]["expect"] == "denied" and (
            not row.get("read_requests") or row.get("read_business_ok") != row["read_requests"]
        ):
            reasons.append(f"security denial check failed: {case['id']}")
        if case.get("search", {}).get("forbidden_document_ids") and (
            not row.get("search_requests")
            or row.get("search_business_ok") != row["search_requests"]
        ):
            reasons.append(f"security search check failed: {case['id']}")
    expected_ids = {case["id"] for case in profile["cases"]}
    if not expected_ids.issubset(rows) or any(
        rows[key].get("search_requests", 0) == 0 for key in rows
    ):
        reasons.append("representative cases have missing observations")
    searches = sum(row["search_requests"] for row in rows.values())
    reads = sum(row["read_requests"] for row in rows.values())
    if not searches or searches != reads:
        reasons.append("no complete balanced search/read workload")
    # Per-operation quality and semantic fallback are independent gates.
    denominator = max(searches + reads, 1)
    business = (
        sum(row["search_business_ok"] + row["read_business_ok"] for row in rows.values())
        / denominator
    )
    degraded = sum(row["vector_degraded"] for row in rows.values()) / max(searches, 1)
    quality = metrics.get("quality", {})
    requests = sum(row.get("requests", 0) for row in quality.values())
    allowed_denials = metrics.get("expected_denial_responses", 0)
    transport = (sum(row.get("http_ok", 0) for row in quality.values()) + allowed_denials) / max(
        requests, 1
    )
    observed = {
        "http_success_rate": transport,
        "business_correctness_rate": business,
        "max_vector_degraded_rate": degraded,
    }
    stats = metrics.get("locust", {})
    for key in ("p50_ms", "p95_ms", "p99_ms"):
        value = stats.get(key)
        if type(value) not in (int, float) or not math.isfinite(value):
            reasons.append(f"{key} is missing")
        else:
            observed[key] = value
            if value > profile["slo"][key]:
                reasons.append(f"{key} exceeds frozen maximum")
    for key in ("http_success_rate", "business_correctness_rate"):
        if not 0 <= observed[key] <= 1 or observed[key] < profile["slo"][key]:
            reasons.append(f"{key} is below frozen minimum or inconsistent")
    if degraded > profile["slo"]["max_vector_degraded_rate"]:
        reasons.append("semantic degradation exceeds frozen maximum")
    return {
        "status": "failed" if reasons else "passed",
        "reasons": reasons,
        "observed": observed,
        "scope": "frozen REST backend sub-gate only; not full Agent or production acceptance",
    }


def load_and_validate(profile_path, credential_path, host):
    profile = _load_json(profile_path)
    credentials = _load_json(credential_path)
    if profile.get("schema_version") != 1:
        raise ProfileError("Unsupported profile schema")
    if profile.get("profile_kind") not in {"development", "acceptance"}:
        raise ProfileError("Unknown profile kind")
    for key in ("users", "spawn_rate", "duration_after_warmup_seconds"):
        if not isinstance(profile.get(key), (int, float)) or profile[key] <= 0:
            raise ProfileError(f"{key} must be positive")
    if profile.get("warmup_seconds", -1) < 0 or profile.get("wait_time_seconds") != 0:
        raise ProfileError("Warmup must be non-negative and this closed-loop profile has no wait")
    if profile["warmup_seconds"] < profile["users"] / profile["spawn_rate"]:
        raise ProfileError("Warmup must include the complete ramp before statistics reset")
    target = profile.get("target", {}).get("base_url")
    if not target or _origin(target) != _origin(host):
        raise ProfileError("Locust host does not exactly match the frozen target origin")
    tokens = credentials.get("tokens") if isinstance(credentials, dict) else None
    if not isinstance(tokens, list) or len(tokens) < profile["users"]:
        raise ProfileError("Profile requires at least one credential per user")
    normalized = [item.get("token") if isinstance(item, dict) else item for item in tokens]
    if any(not isinstance(token, str) or not token for token in normalized):
        raise ProfileError("Credentials must contain non-empty tokens")
    if len(set(normalized)) != len(normalized):
        raise ProfileError("Profile requires unique credentials")
    cases = profile.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ProfileError("Profile requires at least one query case")
    for case in cases:
        _validate_case(case)
    known_cases = {case["id"] for case in cases}
    for credential in tokens:
        if isinstance(credential, dict):
            selected = credential.get("case_ids", [])
            if (
                not isinstance(selected, list)
                or any(not isinstance(value, str) for value in selected)
                or not set(selected).issubset(known_cases)
            ):
                raise ProfileError("Credential case ids must reference existing cases")
    if profile["profile_kind"] == "acceptance" and (
        profile["users"] != 50 or profile["duration_after_warmup_seconds"] != 1800
    ):
        raise ProfileError("Acceptance profile must retain 50 users and 30 measured minutes")
    blockers = formal_acceptance_blockers(profile)
    if profile["profile_kind"] == "acceptance" and blockers:
        raise ProfileError("Formal acceptance is blocked: " + "; ".join(blockers))
    return profile, tokens


def freeze_profile(source, destination):
    data = Path(source).read_bytes()
    # Parse before copying so an invalid artifact can never be called frozen.
    json.loads(data)
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(data)
    return hashlib.sha256(data).hexdigest()


def _degraded(warnings):
    return any(
        "向量" in warning and any(marker in warning for marker in ("不可用", "模型版本", "关键词"))
        for warning in warnings
        if isinstance(warning, str)
    )


def evaluate_search(case, status_code, payload):
    http_ok = 200 <= status_code < 300
    if not http_ok or not isinstance(payload, dict):
        return Evaluation(http_ok, False, reason=f"HTTP status {status_code}")
    items = payload.get("items")
    warnings = payload.get("warnings", [])
    if not isinstance(items, list) or not isinstance(warnings, list):
        return Evaluation(True, False, reason="malformed search response")
    vector_degraded = _degraded(warnings)
    expectation = case["search"]
    forbidden = set(expectation.get("forbidden_document_ids", []))
    if any(str(item.get("document_id")) in forbidden for item in items if isinstance(item, dict)):
        return Evaluation(True, False, vector_degraded, "forbidden source returned")
    if expectation["expect"] == "absent":
        return Evaluation(True, True, vector_degraded)
    if expectation["expect"] == "empty":
        if items:
            return Evaluation(True, False, vector_degraded, "expected no answer")
        return Evaluation(True, True, vector_degraded)
    max_rank = expectation.get("max_rank", 1)
    match = None
    for rank, item in enumerate(items, 1):
        if (
            rank <= max_rank
            and isinstance(item, dict)
            and str(item.get("document_id")) == str(expectation["document_id"])
            and str(item.get("version_id")) == str(expectation["version_id"])
        ):
            match = item
            break
    if match is None:
        return Evaluation(
            True, False, vector_degraded, "expected source is not within allowed rank"
        )
    required = expectation.get("chunk_contains")
    if required and required not in str(match.get("excerpt", "")):
        return Evaluation(True, False, vector_degraded, "expected content is absent from hit")
    if case.get("semantic_only") and vector_degraded:
        return Evaluation(True, False, True, "semantic-only query used degraded retrieval")
    return Evaluation(True, True, vector_degraded)


def evaluate_read(case, status_code, payload):
    expectation = case["read"]
    http_ok = 200 <= status_code < 300
    if expectation["expect"] == "denied":
        business_ok = (
            status_code == 404
            and isinstance(payload, dict)
            and payload.get("error", {}).get("code") == "NOT_FOUND"
        )
        return Evaluation(
            http_ok, business_ok, reason=None if business_ok else "denial contract failed"
        )
    if not http_ok or not isinstance(payload, dict):
        return Evaluation(http_ok, False, reason=f"HTTP status {status_code}")
    if str(payload.get("version_id")) != str(expectation["version_id"]):
        return Evaluation(True, False, reason="wrong version returned")
    if expectation.get("source_version") != payload.get("source_version"):
        return Evaluation(True, False, reason="wrong source revision")
    items = payload.get("items")
    if not isinstance(items, list):
        return Evaluation(True, False, reason="malformed read response")
    text = "\n".join(str(item.get("text", "")) for item in items if isinstance(item, dict))
    if expectation.get("contains") and expectation["contains"] not in text:
        return Evaluation(True, False, reason="expected source content is absent")
    return Evaluation(True, True)

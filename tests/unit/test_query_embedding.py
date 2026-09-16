"""Query admission and execution bounds without loading a native model."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from time import monotonic

import pytest

from agico_kb import embeddings
from agico_kb.config import Settings


class GatedModel:
    identity = "test-model"

    def __init__(self):
        self.entered = {name: Event() for name in ("first", "second", "third")}
        self.release = {name: Event() for name in self.entered}
        self.fail = set()

    def embed(self, texts):
        text = texts[0]
        self.entered[text].set()
        assert self.release[text].wait(3), "test did not release model"
        if text in self.fail:
            raise ValueError("model failure")
        return [[1.0, 2.0]]


@pytest.fixture
def query_factory(monkeypatch):
    resources = []

    def create(timeout=2):
        model = GatedModel()
        monkeypatch.setattr(embeddings, "LocalEmbedding", lambda _: model)
        query = embeddings.QueryEmbedding(
            Settings("unused", Path("."), query_timeout_seconds=timeout)
        )
        callers = ThreadPoolExecutor(max_workers=3)
        resources.append((model, query, callers))
        return model, query, callers

    yield create
    for model, query, callers in resources:
        for gate in model.release.values():
            gate.set()
        query.close()
        callers.shutdown(wait=True)


def assert_waiting(future):
    with pytest.raises(TimeoutError):
        future.result(timeout=0.05)
    assert not future.done(), "contention rejected a query instead of waiting"


def test_contention_waits_then_returns_embedding(query_factory):
    model, query, callers = query_factory()
    first = callers.submit(query.query, "first")
    assert model.entered["first"].wait(1)
    second = callers.submit(query.query, "second")
    assert_waiting(second)
    model.release["first"].set()
    model.release["second"].set()
    assert first.result(timeout=1) == ([1.0, 2.0], "test-model")
    assert second.result(timeout=1) == ([1.0, 2.0], "test-model")


def test_wait_and_inference_share_one_deadline(query_factory, monkeypatch):
    # Controlled elapsed time makes the remaining budget independent of scheduling.
    now = [0.0]
    monkeypatch.setattr(embeddings, "monotonic", lambda: now[0], raising=False)
    model, query, callers = query_factory(timeout=1)
    first = callers.submit(query.query, "first")
    assert model.entered["first"].wait(1)
    second = callers.submit(query.query, "second")
    assert_waiting(second)
    now[0] = 0.85
    started = monotonic()
    model.release["first"].set()
    first.result(timeout=1)
    assert model.entered["second"].wait(0.5)
    with pytest.raises(TimeoutError):
        second.result(timeout=0.6)
    assert second.done(), "inference incorrectly received a fresh timeout budget"
    assert monotonic() - started < 0.6


def test_backlog_cap_rejects_excess_but_preserves_admitted_waiter(query_factory, monkeypatch):
    monkeypatch.setattr(embeddings.QueryEmbedding, "MAX_PENDING", 2, raising=False)
    model, query, callers = query_factory()
    first = callers.submit(query.query, "first")
    assert model.entered["first"].wait(1)
    second = callers.submit(query.query, "second")
    assert_waiting(second)
    excess = callers.submit(query.query, "third")
    with pytest.raises(TimeoutError):
        excess.result(timeout=0.5)
    assert excess.done(), "excess caller was admitted to a full backlog"
    assert not model.entered["third"].is_set()
    model.release["first"].set()
    model.release["second"].set()
    first.result(timeout=1)
    assert second.result(timeout=1) == ([1.0, 2.0], "test-model")


def test_model_failure_releases_execution_for_waiter(query_factory):
    model, query, callers = query_factory()
    model.fail.add("first")
    first = callers.submit(query.query, "first")
    assert model.entered["first"].wait(1)
    second = callers.submit(query.query, "second")
    assert_waiting(second)
    model.release["first"].set()
    model.release["second"].set()
    with pytest.raises(ValueError, match="model failure"):
        first.result(timeout=1)
    assert second.result(timeout=1) == ([1.0, 2.0], "test-model")


def test_close_wakes_waiter_and_rejects_new_queries(query_factory):
    model, query, callers = query_factory()
    first = callers.submit(query.query, "first")
    assert model.entered["first"].wait(1)
    second = callers.submit(query.query, "second")
    assert_waiting(second)
    query.close()
    with pytest.raises(RuntimeError, match="closed"):
        second.result(timeout=0.5)
    with pytest.raises(RuntimeError, match="closed"):
        query.query("third")
    assert not model.entered["second"].is_set()
    model.release["first"].set()
    assert first.result(timeout=1) == ([1.0, 2.0], "test-model")


def test_timed_out_inference_keeps_execution_slot_until_native_work_finishes(query_factory):
    model, query, callers = query_factory(timeout=0.15)
    first = callers.submit(query.query, "first")
    assert model.entered["first"].wait(1)
    with pytest.raises(TimeoutError):
        first.result(timeout=1)
    second = callers.submit(query.query, "second")
    assert_waiting(second)
    with pytest.raises(TimeoutError):
        second.result(timeout=1)
    assert not model.entered["second"].is_set()
    third = callers.submit(query.query, "third")
    assert_waiting(third)
    model.release["first"].set()
    model.release["third"].set()
    assert third.result(timeout=1) == ([1.0, 2.0], "test-model")
    assert not model.entered["second"].is_set(), "expired queued work reached the model"


def test_waiters_run_in_admission_order(query_factory):
    model, query, callers = query_factory()
    first = callers.submit(query.query, "first")
    assert model.entered["first"].wait(1)
    second = callers.submit(query.query, "second")
    assert_waiting(second)
    third = callers.submit(query.query, "third")
    assert_waiting(third)
    model.release["first"].set()
    assert model.entered["second"].wait(1)
    assert not model.entered["third"].is_set()
    model.release["second"].set()
    model.release["third"].set()
    for result in (first, second, third):
        assert result.result(timeout=1) == ([1.0, 2.0], "test-model")


def test_submit_failure_does_not_leak_admission(query_factory, monkeypatch):
    model, query, _ = query_factory()

    def reject(*args):
        raise RuntimeError("cannot start executor")

    with monkeypatch.context() as patch:
        patch.setattr(query._pool, "submit", reject)
        with pytest.raises(RuntimeError, match="cannot start executor"):
            query.query("first")
    model.release["second"].set()
    assert query.query("second") == ([1.0, 2.0], "test-model")
    assert not model.entered["first"].is_set()


def test_timeout_cancels_not_started_job_and_releases_execution(query_factory):
    model, query, callers = query_factory(timeout=0.15)
    # Hold the executor before a query is submitted, reproducing delayed worker
    # scheduling without replacing the actual Future cancellation machinery.
    unblock = Event()
    occupied = Event()

    def occupy():
        occupied.set()
        assert unblock.wait(3)

    blocked = query._pool.submit(occupy)
    try:
        assert occupied.wait(1)
        first = callers.submit(query.query, "first")
        with pytest.raises(TimeoutError):
            first.result(timeout=1)
        assert not model.entered["first"].is_set()
        unblock.set()
        blocked.result(timeout=1)
        model.release["second"].set()
        assert query.query("second") == ([1.0, 2.0], "test-model")
        assert not model.entered["first"].is_set()
    finally:
        unblock.set()

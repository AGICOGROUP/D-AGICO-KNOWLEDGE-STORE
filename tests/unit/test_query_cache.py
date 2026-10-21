"""Fresh in-memory models exercise cache correctness, bounds and contention."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event

import pytest

from agico_kb import embeddings
from agico_kb.config import Settings


class CountingModel:
    identity = "测试数据-query-cache-model"

    def __init__(self):
        self.calls = []
        self.fail = False
        self.entered = Event()
        self.release = Event()
        self.release.set()

    def embed(self, texts):
        self.calls.append(texts[0])
        self.entered.set()
        assert self.release.wait(3)
        if self.fail:
            raise ValueError("temporary model failure")
        return [[float(len(texts[0])), 1.0]]


@pytest.fixture
def cached_query(monkeypatch):
    model = CountingModel()
    monkeypatch.setattr(embeddings, "LocalEmbedding", lambda _: model)
    service = embeddings.QueryEmbedding(Settings("unused", Path(".")))
    yield service, model
    model.release.set()
    service.close()


def test_repeat_reuses_vector_but_never_returns_mutable_cached_storage(cached_query):
    service, model = cached_query
    first = service.query("测试数据：密封温度")
    first[0][0] = -999
    second = service.query("测试数据：密封温度")
    assert second == ([float(len("测试数据：密封温度")), 1.0], model.identity)
    assert model.calls == ["测试数据：密封温度"]


def test_expired_query_is_computed_again(cached_query, monkeypatch):
    service, model = cached_query
    now = [100.0]
    monkeypatch.setattr(embeddings, "monotonic", lambda: now[0])
    service.query("测试数据：到期")
    now[0] += service.CACHE_TTL_SECONDS + 1
    service.query("测试数据：到期")
    assert len(model.calls) == 2


def test_cache_is_bounded_and_evicts_least_recently_used(cached_query, monkeypatch):
    service, model = cached_query
    monkeypatch.setattr(service, "MAX_CACHE_ENTRIES", 2, raising=False)
    for text in ("测试数据甲", "测试数据乙", "测试数据甲", "测试数据丙", "测试数据甲", "测试数据乙"):
        service.query(text)
    assert model.calls == ["测试数据甲", "测试数据乙", "测试数据丙", "测试数据乙"]


def test_simultaneous_identical_queries_share_completed_inference(cached_query):
    service, model = cached_query
    model.release.clear()
    with ThreadPoolExecutor(max_workers=8) as callers:
        first = callers.submit(service.query, "测试数据：并发")
        assert model.entered.wait(1)
        waiting = [callers.submit(service.query, "测试数据：并发") for _ in range(7)]
        model.release.set()
        assert all(future.result(timeout=2) == first.result(timeout=2) for future in waiting)
    assert model.calls == ["测试数据：并发"]


def test_failure_is_not_cached_and_next_call_retries(cached_query):
    service, model = cached_query
    model.fail = True
    with pytest.raises(ValueError, match="temporary model failure"):
        service.query("测试数据：恢复")
    model.fail = False
    service.query("测试数据：恢复")
    service.query("测试数据：恢复")
    assert len(model.calls) == 2


def test_exact_query_keys_do_not_merge_product_identifiers(cached_query):
    service, model = cached_query
    for query in ("AX-210", "AX-210B", "ax-210", "ＡＸ－２１０"):
        service.query(query)
    assert len(model.calls) == 4


def test_long_internal_queries_do_not_consume_cache(cached_query):
    service, model = cached_query
    text = "测试数据" * 150
    service.query(text)
    service.query(text)
    assert len(model.calls) == 2


def test_closed_service_does_not_serve_previously_cached_vector(cached_query):
    service, _ = cached_query
    service.query("测试数据：关闭")
    service.close()
    with pytest.raises(RuntimeError, match="closed"):
        service.query("测试数据：关闭")


def test_cached_query_remains_available_when_native_backlog_is_full(cached_query, monkeypatch):
    service, model = cached_query
    expected = service.query("测试数据：已缓存")
    monkeypatch.setattr(service, "MAX_PENDING", 1)
    model.entered.clear()
    model.release.clear()
    with ThreadPoolExecutor(max_workers=1) as callers:
        running = callers.submit(service.query, "测试数据：占用模型")
        try:
            assert model.entered.wait(1)
            assert service.query("测试数据：已缓存") == expected
            assert len(model.calls) == 2
        finally:
            model.release.set()
        running.result(timeout=1)

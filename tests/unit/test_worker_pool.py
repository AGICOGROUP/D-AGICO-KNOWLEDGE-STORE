from collections import deque
from concurrent.futures import wait
from threading import Barrier, Event, Lock

from agico_kb.config import Settings
from agico_kb.worker import WorkerPool


def test_five_slots_start_in_order_without_blocking_on_first_file(tmp_path):
    queue = deque(range(1, 7))
    barrier = Barrier(6)
    release = Event()
    lock = Lock()
    started = []

    class FakeWorker:
        def __init__(self, *_):
            pass

        def claim(self):
            return {"id": queue.popleft(), "generation": "test"} if queue else None

        def run_claim(self, claim):
            with lock:
                started.append(claim["id"])
            if claim["id"] <= 5:
                barrier.wait(timeout=5)
            release.wait(timeout=5)

    pool = WorkerPool(None, Settings("unused", tmp_path), worker_factory=FakeWorker)
    try:
        pool.tick()
        barrier.wait(timeout=5)
        assert len(pool.active) == 5
        assert sorted(started) == [1, 2, 3, 4, 5]
        assert list(queue) == [6]
        release.set()
        assert not wait(pool.active, timeout=5).not_done
        pool.tick()
        assert not wait(pool.active, timeout=5).not_done
        assert started[-1] == 6
    finally:
        release.set()
        pool.close()


def test_busy_files_keep_lease_alive_from_pool_thread(tmp_path, monkeypatch):
    clock = [0]
    monkeypatch.setattr('agico_kb.worker.time.monotonic', lambda: clock[0])
    release = Event()
    queue = deque([{'id': 1, 'generation': 'fence'}])
    renewed = []

    class FakeWorker:
        def __init__(self, *_): pass
        def claim(self): return queue.popleft() if queue else None
        def run_claim(self, claim): release.wait(timeout=5)
        def renew_lease(self, job_id, generation): renewed.append((job_id, generation))

    pool = WorkerPool(None, Settings('unused', tmp_path), worker_factory=FakeWorker)
    try:
        pool.tick()
        clock[0] = 31
        pool.tick()
        assert renewed == [(1, 'fence')]
        pool.tick()
        assert len(renewed) == 1
    finally:
        release.set()
        pool.close()

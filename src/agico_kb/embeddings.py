"""One bounded local model; file search never needs to load it."""

import hashlib
import json
from collections import OrderedDict, deque
from pathlib import Path
from threading import Lock
from time import monotonic


class LocalEmbedding:
    def __init__(self, settings):
        from fastembed import TextEmbedding

        self.model = TextEmbedding(
            settings.embedding_model,
            cache_dir=str(settings.model_cache),
            threads=2,
            local_files_only=settings.model_offline,
        )
        self.dimension = self.model.embedding_size
        self.tokenizer = self.model.model.tokenizer
        # Hash the actual local model artifacts, not a floating upstream model name.
        root = Path(self.model.model._model_dir)
        digest = hashlib.sha256()
        for path in sorted(
            p for p in root.rglob("*") if p.is_file() and p.suffix in {".onnx", ".json", ".txt"}
        ):
            digest.update(path.relative_to(root).as_posix().encode())
            with path.open("rb") as stream:
                for data in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(data)
        from importlib.metadata import version

        self.identity = f"{settings.embedding_model}:{digest.hexdigest()}:{self.dimension}:fastembed-{version('fastembed')}:native-v1"
        if settings.expected_model_identity and self.identity != settings.expected_model_identity:
            raise ValueError("Model identity does not match configured pin")
        self._lock = Lock()

    def embed(self, texts):
        with self._lock:
            return [vector.tolist() for vector in self.model.embed(texts, batch_size=16)]


def vector_literal(vector):
    import math

    values = [float(v) for v in vector]
    if not values or not all(math.isfinite(v) for v in values):
        raise ValueError("Invalid embedding")
    return json.dumps(values, separators=(",", ":"))


class QueryEmbedding:
    # Includes the active native call; waiting callers never enter the executor queue.
    MAX_PENDING = 64
    MAX_CACHE_ENTRIES = 256
    CACHE_TTL_SECONDS = 300

    def __init__(self, settings):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Condition

        self.settings = settings
        self._model = None
        self._condition = Condition()
        self._waiting = deque()
        # Cache vectors only, never search results or authorization decisions.
        # Each instance owns one immutable model configuration. Exact text keys
        # preserve product identifiers; entries expire and cannot grow unbounded.
        self._cache = OrderedDict()
        self._active = False
        self._closed = False
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="kb-query-vector")

    def _query(self, text):
        if self._model is None:
            self._model = LocalEmbedding(self.settings)
        vector, identity = self._model.embed([text])[0], self._model.identity
        # Publish a successful immutable cache entry before the Future wakes its
        # callers. Exceptions and cancelled, not-started work never get here.
        with self._condition:
            if not self._closed and len(text) <= 500:
                self._cache[text] = (
                    monotonic() + self.CACHE_TTL_SECONDS, tuple(vector), identity
                )
                self._cache.move_to_end(text)
                while len(self._cache) > self.MAX_CACHE_ENTRIES:
                    self._cache.popitem(last=False)
        return vector, identity

    def query(self, text):
        deadline = monotonic() + self.settings.query_timeout_seconds
        ticket = object()
        with self._condition:
            if self._closed:
                raise RuntimeError("Query embedding is closed")
            cached = self._cached(text)
            if cached is not None:
                return cached
            if len(self._waiting) + self._active >= self.MAX_PENDING:
                raise TimeoutError("Query vector backlog full")
            self._waiting.append(ticket)
            try:
                while True:
                    if self._closed:
                        raise RuntimeError("Query embedding is closed")
                    # An identical query ahead of us may have finished while we
                    # waited. Reuse that vector without another native call.
                    cached = self._cached(text)
                    if cached is not None:
                        return cached
                    remaining = deadline - monotonic()
                    if remaining <= 0:
                        raise TimeoutError("Query vector deadline exceeded")
                    if not self._active and self._waiting[0] is ticket:
                        break
                    self._condition.wait(remaining)
                self._active = True
                try:
                    future = self._pool.submit(self._query, text)
                except BaseException:
                    self._active = False
                    raise
                future.add_done_callback(self._release_execution)
            finally:
                self._waiting.remove(ticket)
                self._condition.notify_all()
        try:
            vector, identity = future.result(timeout=max(0, deadline - monotonic()))
            return list(vector), identity
        except TimeoutError:
            # Cancellation cannot stop native work. Its completion callback alone
            # frees execution, so a hung model never builds an executor backlog.
            future.cancel()
            raise

    def _cached(self, text):
        entry = self._cache.get(text)
        if entry is None:
            return None
        expires, vector, identity = entry
        if monotonic() >= expires:
            del self._cache[text]
            return None
        self._cache.move_to_end(text)
        return list(vector), identity

    def _release_execution(self, future):
        with self._condition:
            self._active = False
            self._condition.notify_all()

    def close(self):
        with self._condition:
            self._closed = True
            self._cache.clear()
            self._condition.notify_all()
        self._pool.shutdown(wait=False, cancel_futures=True)


def main():
    """Explicit online provisioning; output identity for protected runtime configuration."""
    import argparse

    from .config import Settings

    parser = argparse.ArgumentParser(description=main.__doc__)
    parser.add_argument("--cache", type=Path, required=True)
    args = parser.parse_args()
    model = LocalEmbedding(Settings("unused", Path("."), model_cache=args.cache))
    print(model.identity)


if __name__ == "__main__":
    main()

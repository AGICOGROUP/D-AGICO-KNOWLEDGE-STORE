"""Append-only failure log.

Any upload that dies mid-transfer, any request that raises, and any parse that fails writes one
line here, so a failure reported by an employee can be explained after the fact instead of leaving
only a generic message in the browser. Logging must never break a request: every write is guarded.
"""

from datetime import datetime
from pathlib import Path

# Local wall-clock time: an operator reads this log next to portal messages in their own timezone.
LOCAL = datetime.now().astimezone().tzinfo


def failure_log_path(settings) -> Path:
    return Path(settings.storage_root).parent / "logs" / "failures.log"


def log_failure(settings, kind: str, message: str, **fields) -> str:
    """Write one tab-free line; returns the path so callers can quote it to the user."""
    path = failure_log_path(settings)
    stamp = datetime.now(LOCAL).strftime("%Y-%m-%d %H:%M:%S")
    details = " ".join(f"{key}={value}" for key, value in fields.items() if value is not None)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(f"{stamp} | {kind} | {message} | {details}\n")
    except OSError:
        pass
    return str(path)

"""Persistent model cooldowns and route failover shared by API and workers."""
import hashlib
import sqlite3
import time
from contextlib import closing, contextmanager

import httpx

COOLDOWN_SECONDS = 1800
now = time.time


def scope(path, endpoint):
    # Replacing a key or changing the preferred model starts a fresh health scope.
    # No credential or authorization decision is stored here.
    return hashlib.sha256((str(path.absolute()) + endpoint + str(path.stat().st_mtime_ns)).encode()).hexdigest()


@contextmanager
def state(settings):
    folder = settings.storage_root / '.vision'
    folder.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(folder/'model-health.sqlite3', timeout=15)) as conn:
        conn.execute('CREATE TABLE IF NOT EXISTS cooldowns (scope TEXT, model TEXT, until REAL, PRIMARY KEY(scope,model))')
        conn.execute('CREATE TABLE IF NOT EXISTS failovers (scope TEXT PRIMARY KEY, until REAL)')
        with conn:
            conn.execute('DELETE FROM cooldowns WHERE until<=?', (now(),))
            conn.execute('DELETE FROM failovers WHERE until<=?', (now(),))
            yield conn


def cooldowns(settings, namespace):
    with state(settings) as conn:
        return dict(conn.execute('SELECT model,until FROM cooldowns WHERE scope=?', (namespace,)))


def failed(settings, namespace, models):
    with state(settings) as conn:
        conn.executemany('INSERT INTO cooldowns VALUES (?,?,?) ON CONFLICT(scope,model) DO UPDATE SET until=excluded.until',
                         [(namespace, model, now()+COOLDOWN_SECONDS) for model in models])


def failover_until(settings, namespace):
    with state(settings) as conn:
        row = conn.execute('SELECT until FROM failovers WHERE scope=?', (namespace,)).fetchone()
        return row[0] if row else None


def start_failover(settings, namespace):
    # Start only after all primary candidates have failed. Concurrent calls and
    # later fallback failures must not extend the route's fixed half-hour period.
    with state(settings) as conn:
        conn.execute('INSERT OR IGNORE INTO failovers VALUES (?,?)',
                     (namespace, now()+COOLDOWN_SECONDS))
        return conn.execute('SELECT until FROM failovers WHERE scope=?', (namespace,)).fetchone()[0]


def credential_failure(error):
    return (isinstance(error, httpx.HTTPStatusError) and error.response.status_code in {401,403}
            or type(error).__name__ == 'VisionQuotaError')


def can_rotate(error):
    if type(error).__name__ in {'VisionOutputTruncated','VisionDeferred'}:
        return False
    if credential_failure(error) or isinstance(error, (httpx.TimeoutException, httpx.TransportError)):
        return True
    if isinstance(error, httpx.HTTPStatusError):
        return error.response.status_code in {400,404,408,429} or error.response.status_code >= 500
    return isinstance(error, (ValueError, KeyError, IndexError, TypeError))


def managed(settings):
    path = settings.provider_profile()
    return bool(path and hasattr(settings, 'provider_keys_directory') and path.parent == settings.provider_keys_directory)

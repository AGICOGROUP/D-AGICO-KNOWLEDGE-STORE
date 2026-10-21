"""One provider request at a time across API, naming and worker processes."""
import errno
import os
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit

_CEPHALON_SLOT = threading.BoundedSemaphore(1)


@contextmanager
def provider_slot(endpoint):
    if urlsplit(endpoint).hostname != 'cephalon.cloud':
        yield
        return
    root = Path(os.environ.get('AGICO_KB_STORAGE_ROOT',
                               str(Path(tempfile.gettempdir()) / 'agico-kb-provider-runtime')))
    folder = root / '.vision'
    folder.mkdir(parents=True, exist_ok=True)
    # A byte-range lock is released by the OS even on process termination.
    # No credential is written, and existing original blobs are never touched.
    with _CEPHALON_SLOT, (folder / 'cephalon-request.lock').open('a+b') as stream:
        if not stream.seek(0, 2):
            stream.write(b'0'); stream.flush()
        stream.seek(0)
        if os.name == 'nt':
            import msvcrt
            while True:
                try:
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError as error:
                    if error.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                        raise
                    time.sleep(.05)
            try:
                yield
            finally:
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(stream, fcntl.LOCK_UN)

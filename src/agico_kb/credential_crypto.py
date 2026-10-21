"""Authenticated encryption for server-side provider keys; no machine binding."""
import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from .provider_config import no_links

PREFIX = 'encrypted-fernet:'


def master_file(profile):
    return Path(profile).parent.parent / '.api-key-encryption.key'


def _cipher(profile, *, create=False):
    path = master_file(profile)
    no_links(path)
    if not path.exists() and create:
        # Missing material for an already encrypted store is an error, never
        # silently replace it and strand the other current credential.
        if any(PREFIX in item.read_text(encoding='utf-8-sig')
               for item in Path(profile).parent.glob('*.txt')):
            raise ValueError('Server encryption material is unavailable')
        try:
            handle = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            pass
        else:
            try:
                with os.fdopen(handle, 'wb') as stream:
                    stream.write(Fernet.generate_key())
                    stream.flush()
                    os.fsync(stream.fileno())
            except BaseException:
                path.unlink(missing_ok=True)
                raise
    try:
        return Fernet(path.read_bytes())
    except (OSError, ValueError):
        raise ValueError('Server encryption material is unavailable') from None


def encrypt(profile, secret):
    return PREFIX + _cipher(profile, create=True).encrypt(secret.encode('utf-8')).decode('ascii')


def decrypt(profile, value):
    if not value.startswith(PREFIX):
        return value  # Legacy read compatibility; operator upgrade encrypts in place.
    try:
        return _cipher(profile).decrypt(value[len(PREFIX):].encode('ascii')).decode('utf-8')
    except (InvalidToken, UnicodeError, ValueError):
        raise ValueError('Provider credential could not be decrypted') from None

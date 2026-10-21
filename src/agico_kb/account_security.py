"""Password hashing without plaintext credential persistence or logging."""
import hashlib
import hmac
import secrets


def hash_password(password: str) -> str:
    if not isinstance(password, str) or not 1 <= len(password) <= 1024:
        raise ValueError('Invalid password length')
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, 600000)
    return f'pbkdf2_sha256$600000${salt.hex()}${digest.hex()}'


def verify_password(password: str, stored: str | None) -> bool:
    try:
        algorithm, rounds, salt, expected = (stored or '').split('$')
        if algorithm != 'pbkdf2_sha256' or not 600000 <= int(rounds) <= 3000000:
            return False
        if not isinstance(password, str) or len(password) > 1024:
            return False
        digest = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), bytes.fromhex(salt), int(rounds))
        return hmac.compare_digest(digest.hex(), expected)
    except (ValueError, TypeError):
        return False

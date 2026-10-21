from uuid import uuid4

from agico_kb.account_security import hash_password, verify_password


def test_password_hashes_are_salted_and_verification_rejects_changed_or_invalid_values():
    password = 'Fresh passphrase ' + uuid4().hex
    first, second = hash_password(password), hash_password(password)
    assert first != second
    assert password not in first
    assert first.startswith('pbkdf2_sha256$600000$')
    assert verify_password(password, first)
    assert not verify_password(password + 'x', first)
    assert not verify_password(password, 'broken')

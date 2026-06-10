"""Password hashing with Argon2id via pwdlib.

passlib is unmaintained and broken on Python 3.13; pwdlib is its
designated successor (FastAPI's own docs moved to it).
"""

from pwdlib import PasswordHash
from pwdlib.hashers.argon2 import Argon2Hasher

_password_hash: PasswordHash = PasswordHash((Argon2Hasher(),))


def hash_password(password: str) -> str:
    """Argon2id hash of the password, ready for users.password_hash."""
    return _password_hash.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """Constant-time verification of a password against a stored hash."""
    return _password_hash.verify(password, password_hash)

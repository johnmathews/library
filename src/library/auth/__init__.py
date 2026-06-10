"""Authentication: Argon2 passwords, cookie sessions, bearer API tokens.

Hand-rolled by design (plan decision 4): pwdlib[argon2] for password
hashing, Postgres-backed opaque session tokens in an httpOnly cookie, and
hashed opaque bearer tokens for automation. No JWT, no fastapi-users.
"""

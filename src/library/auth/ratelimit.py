"""In-process sliding-window rate limiting for login and the OAuth endpoints.

Process-local state is sufficient because the Dockerfile runs ``uvicorn``
with a single worker (no ``--workers``) and compose runs one ``api``
container: every request that must share a counter reaches this one
process. If the app is ever scaled to several workers or replicas, each
holds its own counters and the effective limits multiply by that count.
The counters are lost on restart, which only ever loosens the limit.

State is only touched from the event loop (no awaits between a check and
its update), so no lock is needed. Memory is bounded: expired hits are
dropped on every touch, empty keys are removed, and a hard cap on distinct
keys evicts the least recently touched one (usernames are attacker-chosen,
so the key space is otherwise unbounded).
"""

import math
import time
from collections import OrderedDict, deque
from collections.abc import Callable

from starlette.requests import HTTPConnection

#: Login failures per (case-folded) username, and per client, in the window.
LOGIN_USER_FAILURES: int = 5
LOGIN_CLIENT_FAILURES: int = 20
LOGIN_WINDOW_SECONDS: float = 15 * 60

#: Every request to the OAuth endpoints counts, per client. Token: a refresh
#: per connector per ~hour is typical, so 60 per 15 min leaves room for a
#: dozen clients behind one shared egress IP plus retries. Register: a client
#: registers once per connector setup, so 10 per hour is generous for a
#: household and stops registration-row spam.
TOKEN_REQUESTS: int = 60
TOKEN_WINDOW_SECONDS: float = 15 * 60
REGISTER_REQUESTS: int = 10
REGISTER_WINDOW_SECONDS: float = 60 * 60

#: Hard cap on distinct keys per bucket.
MAX_KEYS: int = 10_000
#: Keys longer than this are truncated (no memory abuse via huge usernames).
_MAX_KEY_CHARS: int = 256


class SlidingWindowLimiter:
    """Named buckets of per-key hit timestamps with a sliding window."""

    def __init__(
        self, clock: Callable[[], float] = time.monotonic, max_keys: int = MAX_KEYS
    ) -> None:
        self._clock = clock
        self._max_keys = max_keys
        self._hits: dict[str, OrderedDict[str, deque[float]]] = {}

    def set_clock(self, clock: Callable[[], float]) -> None:
        self._clock = clock

    def reset(self) -> None:
        """Forget every counter and restore the real clock (test isolation)."""
        self._hits.clear()
        self._clock = time.monotonic

    def _live(self, bucket: str, key: str, window: float) -> deque[float] | None:
        keys = self._hits.get(bucket)
        if keys is None:
            return None
        hits = keys.get(key)
        if hits is None:
            return None
        cutoff = self._clock() - window
        while hits and hits[0] <= cutoff:
            hits.popleft()
        if not hits:
            del keys[key]
            return None
        return hits

    def retry_after(self, bucket: str, key: str, limit: int, window: float) -> int:
        """Seconds until ``key`` may act again; 0 when it is under ``limit``."""
        hits = self._live(bucket, key[:_MAX_KEY_CHARS], window)
        if hits is None or len(hits) < limit:
            return 0
        # The (len - limit + 1)th oldest hit leaving the window frees a slot.
        frees_at = hits[len(hits) - limit] + window
        return max(1, math.ceil(frees_at - self._clock()))

    def now(self) -> float:
        """The limiter's clock reading."""
        return self._clock()

    def record(self, bucket: str, key: str, window: float, at: float | None = None) -> None:
        """Add a hit for ``key`` now (or at ``at``, a reading of ``now()``)."""
        keys = self._hits.setdefault(bucket, OrderedDict())
        key = key[:_MAX_KEY_CHARS]
        hits = self._live(bucket, key, window)
        if hits is None:
            hits = keys.setdefault(key, deque())
        hits.append(self._clock() if at is None else at)
        keys.move_to_end(key)
        while len(keys) > self._max_keys:
            keys.popitem(last=False)

    def forget(self, bucket: str, key: str, stamp: float) -> None:
        """Remove one hit recorded at ``stamp`` (an attempt that turned out fine)."""
        keys = self._hits.get(bucket)
        hits = keys.get(key[:_MAX_KEY_CHARS]) if keys is not None else None
        if hits is not None and stamp in hits:
            hits.remove(stamp)

    def hit(self, bucket: str, key: str, limit: int, window: float) -> int:
        """Count a request and return its ``retry_after`` (0 = allowed).

        A refused request is not recorded, so hammering a limited endpoint
        does not extend the lockout past the original window.
        """
        wait = self.retry_after(bucket, key, limit, window)
        if wait == 0:
            self.record(bucket, key, window)
        return wait


#: The process-wide limiter. Tests reset it between cases (tests/conftest.py).
limiter: SlidingWindowLimiter = SlidingWindowLimiter()


def client_key(conn: HTTPConnection, trusted_header: str | None) -> str:
    """Identify the caller for throttling.

    With ``trusted_header`` set (``LIBRARY_TRUSTED_CLIENT_IP_HEADER``) and
    present, its value is the key; otherwise the socket peer. Behind a
    reverse proxy the peer is the proxy for everyone, so without the header
    all clients share one bucket. Only configure the header when the app is
    reachable solely through the proxy that sets it: a header any client can
    send is a free way to dodge the limit.
    """
    if trusted_header:
        value = conn.headers.get(trusted_header, "").strip()
        if value:
            return value[:_MAX_KEY_CHARS]
    return conn.client.host if conn.client else "unknown"


def begin_login_attempt(username: str, client: str) -> tuple[int, float]:
    """Check the login limits and, when under them, count this attempt now.

    Returns ``(retry_after, stamp)``: a non-zero ``retry_after`` refuses the
    attempt (nothing recorded); otherwise the attempt is already counted
    against the username and the client, and ``stamp`` identifies it for
    ``forget_login_attempt`` if it succeeds. Counting before the password is
    verified (an await) is what stops a burst of simultaneous requests from
    all reading the old count and getting more checks than the limit allows.
    """
    user_key = username.strip().casefold()
    wait = max(
        limiter.retry_after("login-user", user_key, LOGIN_USER_FAILURES, LOGIN_WINDOW_SECONDS),
        limiter.retry_after("login-client", client, LOGIN_CLIENT_FAILURES, LOGIN_WINDOW_SECONDS),
    )
    stamp = limiter.now()
    if wait == 0:
        limiter.record("login-user", user_key, LOGIN_WINDOW_SECONDS, at=stamp)
        limiter.record("login-client", client, LOGIN_WINDOW_SECONDS, at=stamp)
    return wait, stamp


def forget_login_attempt(username: str, client: str, stamp: float) -> None:
    """Uncount an attempt from ``begin_login_attempt`` that succeeded."""
    limiter.forget("login-user", username.strip().casefold(), stamp)
    limiter.forget("login-client", client, stamp)


#: path -> (requests, window seconds) for the guarded OAuth endpoints.
OAUTH_LIMITS: dict[str, tuple[int, float]] = {
    "/mcp/token": (TOKEN_REQUESTS, TOKEN_WINDOW_SECONDS),
    "/mcp/register": (REGISTER_REQUESTS, REGISTER_WINDOW_SECONDS),
}

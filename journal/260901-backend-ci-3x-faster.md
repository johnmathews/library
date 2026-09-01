# Making the backend suite 3x faster

**Date:** 2026-09-01
**Branch:** `backend-suite-speed`

## 1. What went

CI runs 16–23 minutes. The critical path is `backend` (14m) → `build` (2.1m) →
`promote` (0.3m); `e2e` (11.3m) runs alongside and is not yet the long pole.
Inside `backend`, everything except the tests costs ~50 seconds — checkout,
setup-uv, the OCR apt packages (26s), `uv sync` (1.6s, cache warm), the golden
corpus (2.6s), lint. The `Test with coverage` step is **13m 15s** of the 14.

Three changes take the full suite from **551s to 176s** on this machine, with
the same 2115 tests passing and a **byte-identical** `coverage report` — 11284
statements, 601 missing, 95% before and after.

| | full suite, under coverage |
|---|---|
| baseline | 551.1s |
| + `core = "sysmon"` | 309.6s |
| + session-scoped app | ~221s |
| + cheap test hashing | **176.4s** |

## 2. Coverage was costing 75%, for nothing

The suite runs in 314s with no coverage at all and 551s under
`coverage run`. That is not the usual 10–20% instrumentation tax, and the cause
was a line added deliberately:

```toml
concurrency = ["greenlet", "thread"]
```

It was correct when written. SQLAlchemy's asyncio layer runs ORM and DBAPI work
inside a greenlet, the C tracer loses coverage across the switch, and without
that declaration `api/documents.py` measured 55% against a true 99%. The fix
worked. What it also did — invisibly — was pin coverage to the C tracer, because
declaring any concurrency mode other than `thread` rules out the faster core.

`sys.monitoring` (CPython 3.12+, coverage's `sysmon` core) closes the same gap
for free: it attaches to code objects, so a greenlet switch is simply not an
event it can miss, and no concurrency declaration is needed. Swapping
`concurrency` for `core = "sysmon"` and diffing the two full-suite reports
produces no output at all — not "close enough", identical.

The interesting property of this regression is that it is **silent in every
signal the repo has**. The tests pass. The percentage is right. The gate holds.
It shows up only in wall clock, which nothing asserts on. So
`tests/test_coverage_config.py` now asserts the core is `sysmon` and that no
tracer-forcing concurrency mode is declared, with the measured numbers in the
docstring; both guards were confirmed red with the old setting restored, then
green again.

## 3. The suite spent 27% of itself building the same app 559 times

Profiling fixture setup (a throwaway `pytest_fixture_setup` wrapper) rather than
test bodies is what found the rest. On a representative API file, `--durations`
showed **every** entry in the top ten was `setup`, never `call`: 12.6s of setup
against 3.6s of actual test.

Across the whole suite, before:

```
84.13s  n=559  api_app
40.03s  n=415  api_client
27.65s  n=449  auth_user
 9.41s  n= 94  admin_client
 5.55s  n= 94  admin_user
```

`api_app` was function-scoped, so 559 tests each paid ~107ms for `create_app()`.
`cProfile` puts ~98% of that in `include_router` → `get_dependant` →
Pydantic `TypeAdapter` construction, across roughly 250 routes. It is the same
app every time: `create_app()` reads no settings at construction — every
`get_settings()` call in the application happens inside a request or the
lifespan — so the object cannot vary by test.

Session-scoping the instance, with a thin function-scoped `api_app` wrapper that
still points `LIBRARY_DATA_DIR` at the test's `tmp_path` and clears the settings
cache on both sides of the test, removes the fixture from the profile entirely.

## 4. Argon2 at production cost, ~1000 times

Every API test hashes a password (`auth_user` inserts a user) and verifies one
(`api_client` logs it in). Both are ~30ms *by design* — that is what a work
factor is for. At ~500 client tests that is ~30 seconds of deliberate
key-stretching to prove routing and serialisation.

A session-scoped autouse fixture swaps the module's `PasswordHash` for Argon2id
at `time_cost=1, memory_cost=8, parallelism=1`. Same algorithm, same
`hash_password`/`verify_password` code paths, same right-password-succeeds and
wrong-password-fails behaviour — only the work factor drops. `auth_user` falls
27.7s → 9.3s and `admin_user` 5.6s → 1.9s.

## 5. What this does and does not fix

Scaled to the CI runner (~2.4x slower than this machine on the same suite), the
13m15s test step should land around 4–5 minutes, which takes `backend` off the
critical path and makes **`e2e` the long pole at 11.3m**.

Two measured e2e findings are recorded here but deliberately not acted on in
this PR, because unlike the above they cannot be verified locally — a local e2e
stack drifts against the built image and produces phantom failures:

- **`--with-deps` wastes ~75s per run.** The Playwright browser cache *hits*
  (474MB restored in 8.5s) and `npx playwright install --with-deps` then runs a
  full `apt-get update` and install anyway.
- **`workers: 1` is 389s of the 407s test step.** Per project: tablet-webkit
  148.6s, mobile-webkit 144.3s, chromium 84.4s, firefox 6.0s, webkit 5.7s.
  Sharding by project across a CI matrix — rather than raising `workers` inside
  one job — keeps today's serial-within-a-project semantics exactly, which
  matters because 15 of 26 specs assert on `.first()` of a library-wide list and
  only 18 seed a unique marker. Longest shard ≈ 149s of tests plus ~165s of
  setup.

Not attacked here, and the next thing worth measuring if the backend job ever
matters again: `api_client` is still 23s across 415 tests, almost all of it
`TestClient` entering the app lifespan per test (the events broker's Postgres
`LISTEN` connection, `job_app.open_async()`, the mounted MCP app's lifespan).
That one is riskier than anything above — the lifespan owns real connections —
so it wants its own change, not a rider on this one.

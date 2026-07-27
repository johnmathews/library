# The coverage report was wrong by six points, and a fastmcp security bump

Follow-on to `260727-critical-fixes-resume-quote-cigate.md`. Two small units from the
same 27-unit plan, both one-liners in effect, both shipped together because they touch
`pyproject.toml`.

## 1. fastmcp >= 3.4.4

**Grading: confirmed** against the upstream release notes, fetched rather than recalled.

3.4.3 fixes three security issues in a server this app mounts into its own FastAPI
lifespan (`app.py`, `create_mcp_http_app`):

- IPv6-transition SSRF bypasses — NAT64, 6to4, Teredo and ISATAP addresses could smuggle
  private IPv4 targets past the SSRF allow-list
- DNS rebinding — Streamable HTTP now validates `Host` and `Origin` before session handling
- OAuth redirect validation now rejects unsafe schemes and unregistered DCR redirect URIs

Pinned `>=3.4.4`, not `>=3.4.3`, and that distinction is the whole reason this needed a
moment's thought: **3.4.3's Host/Origin guard changed the default for ASGI, serverless and
reverse-proxy deployments**, which is exactly this deployment's shape. 3.4.4 restores the
compatibility. Resolves to 3.4.5, whose five changes are unrelated bug fixes with no
behavioural change to HTTP mounting — checked, not assumed.

## 2. The coverage tracer

**Grading: confirmed.** Measured before and after on the same 1370 tests.

`pyproject.toml`'s `[tool.coverage.run]` carried a comment blaming FastAPI's TestClient
anyio blocking portal for async handler bodies reading as uncovered, and concluded
*"Fixing the measurement itself is a separate follow-up."*

Both halves were wrong. The cause is **SQLAlchemy's asyncio layer running ORM and DBAPI
work inside a greenlet**, across which coverage loses the tracer unless `greenlet` is a
declared concurrency mode. A minimal FastAPI app with a real suspending `await` traces to
100% under TestClient with the default tracer — so the portal is not implicated. And the
fix is one config line, not a project.

```
default tracer:                       TOTAL 9279  1062  89%
concurrency = ["greenlet", "thread"]: TOTAL 9279   436  95%
src/library/api/documents.py:         55% → 99%
```

**Why this mattered more than six percentage points.** The comment trained every reader to
dismiss any low API-module number as a known artifact. Under that rule a genuinely
untested handler and a mis-measured one are indistinguishable, so the report could not be
used to find real gaps at all — in a repo with 32k lines of tests. The number was the
smaller problem; the excuse was the bigger one.

`fail_under` re-baselined 85 → 93. At 85 the gate carried ten points of slack — an entire
subsystem could have landed untested without turning it red. 93 leaves ~2 points, roughly
one new 200-statement module at 0%.

Proven live rather than asserted: `coverage report` exits 0 at 93 and exits 2 at 96
("total of 95 is less than fail-under=96").

`scripts/coverage_summary.py` had a single `THRESHOLD = 85.0` feeding **both** sides, and
it is what the admin Coverage view displays — so without splitting it the panel would have
reported a stale backend gate. Now `BACKEND_THRESHOLD = 93.0` / `FRONTEND_THRESHOLD = 85.0`.
The frontend's own thresholds live in `frontend/vitest.config.ts` and are untouched.

## 3. A method note worth keeping

While proving the gate could fail, I first ran `coverage report | tail -2` and read `$?` —
which reports **`tail`'s** exit status, not `coverage`'s. It printed a failure message
alongside `EXIT=0`. This is the identical hazard the repo's own CI review flagged in
`ci.yml`, committed by the person checking for it, one day later. Re-ran without the pipe.

The general form: a fresh shell does not inherit `set -o pipefail` from a previous
command, so any `cmd | tail` whose exit code is the evidence is unsafe by default.

## 4. What is deliberately not done

1. **`rapidocr==3.8.2` is yanked** — surfaced by `uv lock`: *"missing arch_config.yaml
   causing PyTorch engine failure"*. Not fixed here because it is not this unit's scope,
   but it deserves its own pass and it compounds a known gap: `tests/test_ocr_real.py`
   swallows **any** RapidOCR init exception as a `pytest.skip`, so a broken photo-OCR
   engine makes CI greener rather than redder. A yanked release plus a skip-swallowing
   test is the exact blind spot. Upgrade target is 3.9.2.
2. **The 21 remaining plan units** — the gitignored real-document test corpus, mypy, local
   dev on arm64, the docs stamp gate, per-test DB isolation.
3. **The double CI run** — every PR still runs the whole ~30-minute pipeline twice
   (`ci.yml` triggers on both `push: ["**"]` and `pull_request`, in different concurrency
   groups). One-line fix, but it changes when feature-branch pushes are gated, so it wants
   its own decision.
4. **Re-baselining the frontend gate.** Only the backend tracer was wrong; the frontend
   number is honest and was left alone.

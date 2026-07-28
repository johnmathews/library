# The default that was a password, and the dependency that was a lie

W6 and W10. Both are about configuration that claimed one thing and did another: a compose
dependency that provided no ordering while breaking an entire architecture, and a settings
file that promised completeness while omitting 21 entries — one of which shipped a real
password.

## 1. W6 — `docker compose up` was broken on Apple Silicon

`api` and `worker` both declared `depends_on: embedder: condition: service_started`. On arm64,
text-embeddings-inference publishes no image, so the pull fails and **neither service starts** —
over a dependency neither of them needs. `--no-deps` was the standing workaround.

The dependency was wrong on its own terms, twice over:

1. **It was vacuous.** `service_started` means the container process began, not that bge-m3
   finished loading (a ~2 GB download on first run). It never provided the ordering it looked
   like it provided.
2. **It contradicted a stated invariant.** `jobs.py` says embedding "must never stop a document
   reaching `indexed`: when the embedder is unreachable, the reason is recorded as an event and
   swallowed" — and implements exactly that. So the service that must not block ingest was
   declared as a hard prerequisite for the processes that perform it.

The evaluation named only `worker`. Fixing one leaves `docker compose up` broken, so both went.

**`platform: linux/amd64` was assessed and rejected.** TEI's CPU images target AVX/AVX2, which
Rosetta does not reliably provide, so the likely outcome is a crash-loop under
`restart: unless-stopped` — strictly worse than an honest "no matching manifest" pull error,
because a crash-loop looks like a bug in Library.

Added `make up` / `make down` around the four services that serve the app
(`db migrate api worker`) — the same subset `ci.yml`'s e2e job has used all along — plus the
`GIT_SHA` build arg on all three built services, since compose builds a separate image per
service.

**Grading: confirmed on the hardware in question.** `make up` on this arm64 Mac reaches a
healthy stack with no embedder container created at all, and:

```
$ curl -s localhost:18099/healthz
{"status":"ok","version":"0.1.0","git_sha":"2140ddf"}
$ git rev-parse --short HEAD
2140ddf
```

Previously `git_sha` was `null` on every locally built image — indistinguishable from a deploy
that failed to stamp. (Port 18099 rather than 8000 because a `library-*` stack was already
bound to 8000; the run was on an override so as not to disturb it.)

## 2. W10 — a real password as a committed default

`config.py` shipped `pdf_unlock_passwords = ["2064"]` — a four-digit personal document
password, as the default, in a **public repository**. W10 would have made it more prominent by
documenting it in `.env.example`, which is what surfaced it as a decision rather than a docs
task.

Now `[]`. With no passwords configured only the empty password is tried, so a genuinely
encrypted PDF is rejected at ingest with a clear `PdfLockedError` instead of being opened with
a credential nobody set.

Removing the default is the easy half. The value was also in **six test files** as the
password they encrypt fixtures with — three of which depended on the *default* being that
value, so they would have silently started testing nothing. Those now set
`LIBRARY_PDF_UNLOCK_PASSWORDS` explicitly (the `cli_data_dir` fixture does it once for the
`sweep-encrypted` suite), which is better anyway: a test that leans on an application default
is not testing the behaviour it names.

**What this does not fix, stated plainly:** the value remains in git history, and in two
journal entries from July that record it as the default. Rotating whatever it protects is the
only real remediation, and that is the owner's to do. The journal entries were left alone
deliberately — they are a dated record of a decision, and quietly editing them to hide a
credential makes the record false without making the credential safe.

### The completeness gap

98 settings; 77 documented. The 21 missing included the entire Recently-Deleted purge lifecycle
(`DELETED_RETENTION_DAYS`, `DELETED_PURGE_ENABLED` — one of which can delete every soft-deleted
document at once if set negative), both Smart Groups thresholds, both series-autocontinue
knobs, the matter-classifier model and its separate budget, and the embedder's batch/timeout.
An operator could not discover any of them from the file that claims to list them all.

All 98 are now documented, which lets the drift test be a **bare subset check with no exemption
list**. The three build-injected settings (`git_sha`, `docs_dir`, `coverage_summary_path`) sit
under an explicit `# --- Internal / build-injected — do not set ---` heading rather than being
exempted in the test: they are real `LIBRARY_`-prefixed fields an operator can set and break,
and the alternative traded one unenforced list (the file) for another (an `_INTERNAL_FIELDS`
set in the test).

Three tests, and the second one is the interesting one:

- `test_env_example_documents_every_setting` — both directions, so a *renamed* setting leaving a
  stale line is caught too.
- `test_env_example_has_no_live_values` — the file had exactly one uncommented assignment,
  `LIBRARY_PUBLIC_BASE_URL=https://library.example.com`. Because compose reads this file for
  `${...}` interpolation, that live line pointed every Pushover deep-link at a domain the
  operator does not own **and**, by being set, suppressed the startup warning that would have
  said so.
- `test_pdf_unlock_passwords_default` — pins the empty default, with `_env_file=None` so a
  developer's real `.env` cannot make the assertion about their machine instead of the code.

`.env.example` and `Makefile` were added to `ci.yml`'s `backend` path filter: without that, a PR
touching only `.env.example` skips the test that guards it.

## 3. Result

1425 passed, coverage 95% against the 93% gate.

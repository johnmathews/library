# Isolation from the framework, and characterisation against real documents

W20 and W11. Both replace "each file arranges its own correctness" with something the suite
guarantees — and W11's recording run turned up a fact about the corpus that contradicts the
plan.

## 1. W20 — the shared database was never cleaned

`api_database_url` is **session-scoped**: one migrated database for the whole API suite. So
every test inherited the rows of every test before it. Combined with the default `limit=25` on
list endpoints, assertions on totals and on `.first()` were silently order-dependent — and two
files had already grown their own `delete(Document)` workarounds, which is the smell that says
the framework is missing something.

**Per-test rollback was assessed and is genuinely impossible here**, not merely inconvenient.
`conftest.py` builds the engine *inside the dependency* with `NullPool`, and the docstring says
why: TestClient runs the app in its own event loop and asyncpg connections are loop-bound. A
test therefore cannot own a connection the app's sessions join. Truncation is the shippable
answer.

Three decisions in the implementation, each load-bearing:

- **A sync psycopg connection**, session-scoped. Sidesteps the loop-binding question entirely
  rather than reasoning about it, at a cost of one connection for the whole run.
- **Teardown, not setup.** A setup-time truncate would delete the `auth_user` row `api_client`
  had just created for the test about to run.
- **No `RESTART IDENTITY`.** Sequences stay monotonic, so a stale id held by one test can never
  coincidentally match a live row from another — it 404s loudly instead of quietly addressing
  someone else's data.

Procrastinate's job tables are named explicitly: they are not in `Base.metadata`, so without
them a test's deferred jobs leak into the next test's job assertions.

**Measured, because the plan set a budget.** Same command, same tree, warm caches:

| | wall clock | result |
| --- | --- | --- |
| truncation off | 365.61 s | **6 failed** |
| truncation on | 380.10 s | 1427 passed |

**+14.5 s**, inside the plan's <15 s allowance. The 6 failures in the baseline are the point:
they are the two files whose workarounds I removed plus the new isolation test, which confirms
the workarounds were load-bearing and truncation now does their job.

The new `tests/test_conftest_isolation.py` is two tests that only mean anything as an ordered
pair — seed through the real API, then assert nothing survived. Mutation-checked: with the
fixture disabled it fails `assert 3 == 0`.

## 2. W11 — characterisation against 15 real documents

Synthetic fixtures keep the suite hermetic but mean the routing thresholds were only ever
tested against documents *we* generated. Real scanner exports and real government PDFs are
where those thresholds actually get decided.

The corpus is **fetched, never committed** — real financial and personal records, public repo.
It lives in a private sibling repo, checked out into `samples/`. The honest cost is that fork
PRs cannot run these tests, and the mitigation is the part that matters: under
`LIBRARY_GOLDEN_CORPUS=1` a missing corpus is a **hard error, not a skip**. Verified in all
three modes — passes with the corpus, skips without it, raises when required and absent.

### The tiering was forced by what is actually deterministic

I first wrote Tier 2 as an engine-level snapshot: run `run_ocr`, record
`(engine, pages, confidence band, text bucket)`. It recorded cleanly — and every one of the 16
documents came back `text-layer-fallback`, which only happens when tesseract *raises*.

**Grading: confirmed, and the cause was my machine.** `tesseract --list-langs` here returns
`eng, osd, snum` — no `nld`, which CI installs as `tesseract-ocr-nld`. So the snapshots encoded
*a missing language pack* as expected behaviour and would have gone red in CI for a reason
having nothing to do with the code. I deleted them.

So Tier 2 snapshots the **routing decision** instead, from `analyze_pdf` — pure pypdfium2
parsing, no subprocess, no model, no language pack, identical on a laptop and in CI. It pins
what `_route_pdf` decides, which is the part that is ours. Engine-level snapshots need a
CI-shaped environment to record; that is stated in `docs/ingestion.md` rather than faked.

### What the corpus said, versus what the plan expected

The plan's acceptance criterion was that Tier 2 assert `engine == "text-layer"` for the
born-digital Belastingdienst PDF. **That is wrong, and the corpus says so.** Those files carry
~2,470 characters per page — plenty — but their pages are *image-backed*, so `scan_like` is
true and they correctly route to OCR for a redo. Of the 16 files, 6 route to the text layer and
10 to OCR, and **10 are scan-like while also having a usable text layer**: a scanner app
embedding its own OCR over image pages. That is precisely the case a plausible-sounding rule
("enough characters means born-digital, trust the layer") gets wrong, and
`test_scan_like_is_not_merely_a_text_length_proxy` now pins it.

### Tier 1 replays rather than mocks

`extractor._attempt` is the seam: one `messages.parse` in, one `(metadata, usage)` out.
Replacing it leaves `build_user_content`, `_thin_scan_prefers_vision` and both escalation
switches running for real while the LLM becomes deterministic — which is the reason to
intercept there rather than mocking `extract` wholesale.

The cassette key is `(model, sha256(content))`. That is not incidental: a prompt or
`build_user_content` change **misses** the cassette and raises, instead of replaying an answer
to a question no longer being asked. A cassette that keeps passing through a prompt change
records the regression rather than catching it.

Pinned: `(input_mode, escalated, kind_slug, review_status, sorted(rules))`. Not pinned:
amounts, dates, senders, summaries — the model's judgement, which moves with any prompt change
and would make the suite flaky and therefore deleted. `today` is fixed at 2026-07-28 so
`date_plausibility` cannot make the suite fail months from now for no code reason.

### The skip that would have gone unnoticed

Tier 1 skips until the cassettes are recorded, and a skip reports as success — the exact hole
W21 closed for the OCR engines. Rather than promise a follow-up commit, the CI step is
**self-arming**:

```yaml
if: steps.corpus.outcome == 'success' && hashFiles('tests/golden_cassettes.json') != ''
```

`hashFiles` returns `''` while the file is absent, so the floor is skipped today and enforces
from the moment cassettes land. The action that creates the tests is the action that arms the
gate on them — no promise to remember.

## 3. Result

1427 passed with no corpus (7 skipped: the golden tiers); **1446 passed** with the corpus
present and required, 3 skipped pending cassettes. Coverage 95% against the 93% gate.


## 4. Postscript: the baselines were nearly published

The recorder ran clean and produced `golden_cassettes.json` and
`golden_extraction_snapshots.json`. I had designed both to be **committed to this
public repository**, on the reasoning that they contain "the model's structured
output only, never document bytes". That reasoning was wrong, and it is worth
recording exactly how, because it was a comment asserting safety rather than a
check establishing it.

What the files actually held:

- the snapshots: **every document's full OCR text**, ~153,000 characters across
  the corpus — insurance policies, tax records, a credit-card missed-payment
  notice
- the cassettes: the model's titles, summaries, senders and amounts, including a
  medical screening invitation, plus `addressee_raw` and `signer_raw`
- the snapshot **keys**: the real filenames, which name the owner's insurer, bank,
  tax advisor and garage

"No document bytes" was true and irrelevant. Text is not safer than bytes; it is
worse, because it is readable and indexable. And the routing snapshot — 3.7 kB,
seemingly just counts and booleans — leaked all 16 filenames, and I had already
committed and pushed it to the PR branch of a public repository.

The fix is a rule blunt enough to have no judgement call in it: **anything derived
from the corpus lives with the corpus.** All three baselines moved into the private
repo; this one keeps the test code. The `hashFiles` self-arming condition moved to
`samples/` with them, which still works because that expression is evaluated when
the step is reached — after the corpus checkout.

The lesson generalises past this unit. The plan's non-goal #3 said "not committing
`samples/` to this repository … this is the one irreversible decision in the whole
plan and the answer is no." I honoured that for the documents and then walked
straight into it for everything *derived* from them, because I had reasoned about
the artifact I was creating rather than the information in it. A privacy boundary
stated in terms of a file type does not survive contact with a file of a different
type.

Recorded distribution, for the record: 14 documents extract from text and 2 take
the vision path; 8 kinds; `missing_recipient` fires 7 times and `missing_sender`
once; total spend $0.1173.

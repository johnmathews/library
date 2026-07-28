# Three defect generators, each replaced by a derivation

W7, W17 and W19 from the 27-unit plan. Grouped because they share a shape rather than a
file: each was a hand-maintained list or an unstated precondition that drifted from the
thing it was supposed to track. In every case the fix is to derive the fact rather than
restate it, and the test asserts the derivation — not the current value.

## 1. W7 — four copies of one refresh list

`_detail()` reads relationships that a `commit()` expires. Reading an expired `selectin`
relationship from async code raises `MissingGreenlet`, so every commit-then-`_detail`
endpoint refreshed first — from its own hand-written list. There were **four**, and they
had all drifted:

| Site | List |
| --- | --- |
| `documents.py` PATCH | 8 attrs |
| `documents.py` restore | `["events", "updated_at"]` |
| `documents.py` verify | bare `refresh()` — reloads *every* column, `ocr_text` included |
| `notes.py` (×4 call sites) | its own 8-attr copy |

The 2-attr one is correct only by accident of what `restore` mutates. The bare one is
over-broad and slow. Adding a field to `_detail` meant remembering all four.

Replaced with one mapper-derived constant — `selectin` relationships plus columns with a
SQL `onupdate` — and one helper. Both classes are enumerable from the model, which is why
this is derivable at all.

**Grading: confirmed.** The derivation surfaces `uploader`, which every hand-written copy
omitted. It is not in the detail payload today, so nothing was broken — but that is luck,
not design, and it is exactly the drift the constant now prevents. Kept deliberately: the
alternative is deriving from what the response happens to read, which is not mechanically
knowable and lands straight back on a hand-maintained list. Cost is one small SELECT.

Two guards, both mutation-checked: hand-editing the constant to drop `matters` reds the
derivation test; a new endpoint hand-rolling `await session.refresh(document, [...])` reds
a structural scan asserting that call appears exactly once in the whole API package.

## 2. W17 — an empty retry stealing the credit

`_tesseract_with_gate` keeps the RapidOCR retry when it yields ≥0.8× Tesseract's character
count. With both texts empty that reads `0 >= 0.8 * 0` — **true** — so the retry won.

**Correction to the evaluation, recorded:** this does not blank the document. Both branches
return empty text; the outcome is identical. What it corrupts is **provenance** —
`replace(retry, …)` stamps `engine="rapidocr"` and RapidOCR's confidence onto a result
RapidOCR did not produce, and that flows into the `ocr_completed` event,
`document.ocr_confidence`, and thence the `ocr_confidence_gate` validation rule, which then
nags about the wrong engine's score. The blank-`indexed` outcome belongs to W9/W3.

Precondition tests `.strip()`, not length: a whitespace-only retry can be *longer* than the
primary and would otherwise pass the ratio test. Both cases are tested; both fail on the
old code with exactly `- tesseract / + rapidocr`. The five existing gate tests were
untouched and stayed green under the mutation.

## 3. W19 — .docx ingested on upload, invisible in the consume folder

`.docx` support shipped 2026-07-07 and was threaded through `detect_mime`, `run_ocr` and
`ALLOWED_MIME_TYPES` — but not `consume.SUPPORTED_EXTENSIONS`, a bare frozenset of
extensions. A `.docx` dropped in the consume folder was ignored **without a trace**: a
non-candidate is never read, so it is not even moved to `failed/`.

`_is_candidate` is a pure filename pre-filter; the real type decision is `detect_mime` on
content. So the fix is not just adding `.docx` — it is making the relationship to the MIME
allowlist *expressible*: `EXTENSION_TO_MIME` maps extension → expected MIME, and
`set(values()) == ALLOWED_MIME_TYPES` is asserted. That equality is the non-recurrence
guard, and it holds in both directions — an allowed MIME with no extension is invisible to
the folder; an extension mapping to a rejected MIME passes the filter only to be refused
after the read.

**The existing test asserted the bug as correct behaviour** and had to be rewritten. It was
worse than wrong: it used `.docx` with body `b"not ours"`, which is UTF-8-decodable, so had
the filter let it through it would have sniffed as `text/plain` and ingested. The test would
have passed either way — its real subject was never the extension. Replaced with `.epub`
and non-decodable bytes, plus an explicit `path.suffix not in SUPPORTED_EXTENSIONS`
precondition so the test states what it is actually testing.

## 4. Result

1397 passed (+6 over 1391), coverage 95% against the 93% gate. Every new guard was
mutation-checked — the `.docx` removal reproduces the original bug as `assert 0 == 1`.

The common thread: all three were **restatements of a fact that lived somewhere else**. A
restatement cannot be verified, only re-checked by hand, and the hand eventually forgets.

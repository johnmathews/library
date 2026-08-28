# The OCR weights were never in the image

GH #109. Photo OCR needed three ONNX models that shipped in neither the wheel nor our
image, and fetched two of them from `modelscope.cn` the first time an engine was built.
`modelscope.cn` went unreachable on 2026-08-28, which reddened CI while merging #108 — a PR
that touches the facet labeller and nothing near OCR. The red build was the cheap half of
the problem.

## 1. The expensive half

The deployed stack had the same dependency, with no cache that survives a container
recreate. `docker-compose.yml` declares `embedder_cache:/data` for the embedder and nothing
for RapidOCR, so whatever the worker downloaded lived in the container filesystem until the
next `--force-recreate` destroyed it. Confirmed on the host after the deploy:
`find / -name '*PP-OCRv5*'` returned nothing. The models were simply absent.

What makes it worse than a missing dependency is *when* it announces itself.
`photo.get_engine()` is lazy and `lru_cache`d, so nothing constructs an engine at startup.
A deploy comes up, `/healthz` says `ok`, every container healthcheck passes, and the failure
waits for the first JPEG, PNG, HEIC or gate-retried scan somebody uploads — then fails that
one document, silently, hours later. Every path in `router.py` that routes to `photo` was
exposed: three MIME types plus any Tesseract result below the confidence threshold.

## 2. Why the obvious fix was not the one taken

The issue's first recommendation was a Dockerfile step that builds the engine once so the
weights land in a layer. Sound, and it fixes production. It does not fix CI: the `backend`
job runs pytest on the runner, not inside the image, so a cache-cold run still hits
modelscope and `check_engine_skips.py` still reds the build. It also could not be *verified*
on 2026-08-28, because a build that downloads from a host that is down does not build.

Recommendation 4 — mirror the files somewhere we control — fixes both, and the cheapest
mirror that needs no hosting is the repository. Checked before committing to it:

- It is exactly three files, ~13.3 MB. PP-OCRv5's rec model embeds its character dict in
  the ONNX metadata, so there is no separate `dict_url` to chase.
- rapidocr's `Global.model_root_dir` is settable through the same params dict as the pins,
  and its downloader skips a file that already exists with the expected SHA256. Verified by
  building the engine offline against a staged directory.
- The three files already on this laptop hash to exactly the SHA256s rapidocr's model list
  pins. They are authentic, which matters, because with modelscope down there was no second
  source to check them against.

So: `models/ocr/` in git, `COPY models/ /app/models/` in the Dockerfile, and
`library.ocr.weights` pointing rapidocr at it. Nothing downloads — not at run time, not at
build time, not in CI, not on a fresh laptop.

The price is honest and worth naming, and worth measuring rather than estimating. The three
files gzip -9 to ratios of 0.87–0.94, so git stores most of the 13.3 MB verbatim. Measured
by committing them and running `git gc --aggressive --prune=now`: the pack goes from
**2.69 MiB to 15.91 MiB**. (Predicting it from the gzip ratios gave 14.9 MiB — git's zlib
is a little less effective than `gzip -9`, so trust the gc number, not the arithmetic.)
Deltas will not help either — different weights do not delta against each other — so every
future revision costs close to full size, permanently.

But the rate is much lower than that framing suggests. Pulling every rapidocr 3.x wheel from
PyPI and diffing the SHA256 our three pins resolve to:

| rapidocr | date | our three models |
| --- | --- | --- |
| 3.0.0 → 3.7.0 | 2025-06 → 2026-03 | not in the model list at all |
| **3.8.0** | 2026-04-08 | introduced |
| 3.8.1 … 3.9.2 | 2026-04 → 2026-07 | unchanged, all eight releases |

One content state, ever. The upstream URL embeds the rapidocr version tag and so moves every
release, but `pinned_models()` compares the file's SHA256 rather than its URL, so a version
bump on its own creates no blob. At roughly one revision per year or two, this is not a
recurring tax.

Git LFS was considered and rejected. It would remove ~12 MiB from the pack and add a failure
mode worse than the one it solves: without `git lfs pull` the working tree holds 130-byte
pointer files, `COPY models/` bakes *those* into the image, and "the weights are not really
there" is reinvented one layer down — the exact bug this whole change closes. It would also
put lfs on the critical path of every clone and CI checkout, and bill a public repo's
bandwidth to its owner. Not worth it for 12 MiB.

The one piece of housekeeping that IS worth it does not target total size. It targets the
only avoidable growth: `test_the_directory_holds_the_pinned_weights_and_nothing_else`
asserts `models/ocr/` contains exactly the pinned `*.onnx` and nothing besides, so a bump
that adds new weights while leaving the superseded ones behind pays for the change once
instead of twice. Verified red by dropping a stray `.onnx` in and watching it name the
extra file. Scoped to `*.onnx` so a stray `.DS_Store` — not a history problem — does not
trip it.

## 3. Keeping the pins and the files from drifting

Vendoring creates a failure the download did not have: the committed file can become the
wrong file. A rapidocr release that repoints a pinned stage used to mean "the engine quietly
downloads something nobody chose" — the exact hazard the twelve pins were added for in
[260728](260728-rapidocr-bump-and-skip-floor.md). Vendoring converts it into "the committed
file no longer matches the pin", which is only better if something looks.

`weights.pinned_models()` therefore restates nothing. It asks rapidocr's own model list what
each pinned stage resolves to and derives the filename and SHA256 from the answer, so
`tests/test_ocr_weights.py` compares the committed bytes against whatever the installed
rapidocr currently demands. A bump that moves a model turns that test red with the stage
named. `python -m scripts.fetch_ocr_models` is the recovery, and is the only thing in the
project that still needs the network.

## 4. Three guards, because committed is not shipped

The [recall baseline](260827-baseline-not-in-image.md) was committed, deployed, and absent
from the running container, silently. Same shape here, so the same countermeasures:

- **`/healthz` reports `ocr_models`** and degrades on a missing weight. Existence only, no
  hashing — the container healthcheck polls this every 10 seconds and the files are 13 MB.
  This is the guard that converts "fails on the first photo, hours later" into "the deploy
  says so".
- **`compose-smoke` runs `scripts/fetch_ocr_models --check`** inside `api` *and* `worker`.
  The worker matters more: it is the process that runs OCR, and an api-only check would
  report a healthy deploy while every photo ingest failed. `--check` verifies checksums, so
  a truncated layer fails too.
- **`tests/test_ocr_weights.py`** covers the pin/file agreement above, plus the two branches
  that only exist for the failure: `missing_models()` against an empty directory, and
  `--check` against files of the right name and the wrong bytes.

## 5. A skip that lost its reason

`require_rapidocr_engine` skipped on `OSError`/`DownloadFileException`, and that was
correct: fetching weights from a third-party hub was the one thing about the test genuinely
outside our control. It is not outside our control any more — the weights arrive with the
checkout — so the branch has no reachable cause, and a skip branch with no reachable cause
is an invisible pass waiting for someone to lean on it. It is gone. Every failure now fails,
including the two exception types that used to buy silence; they stayed in the
`TestRapidOcrGuard` parametrize rather than being deleted, since the point of the change is
that they no longer earn a skip.

A missing weight is checked *before* construction, deliberately. rapidocr answers a missing
weight by trying to download it, so leaving it to rapidocr would report a network error
against a dead host for what is really a broken checkout — regenerating the exact confusion
this issue was about.

`rapidocr` stays in `check_engine_skips.py`'s required list even though its guard now has no
skip branch to catch. That list says what CI must exercise, not what can currently evade it.

## 6. What was not done

The named cache volume (recommendation 3) is moot: nothing downloads, so there is nothing to
persist. Recommendation 5 — teach the CI guard to distinguish "engine broken" from "model
host unreachable" — largely dissolved as the issue predicted, since the reason it was meant
to disambiguate can no longer be produced.

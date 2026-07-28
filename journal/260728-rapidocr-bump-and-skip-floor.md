# The narrowed except caught the bump it shipped with

W21 from the 27-unit plan, bundled with the yanked-`rapidocr==3.8.2` bump because the two
are the same story: W21 removes a guard that turns "our integration is broken" into
"skipped", and the bump is exactly the event that guard was hiding. Bundling them was
meant to be a convenience. It turned into the unit's own regression test.

## 1. What the bump actually did

`rapidocr==3.8.2` was yanked from PyPI — *"missing arch_config.yaml causing PyTorch engine
failure"*. The reason is PyTorch-specific and every stage here is pinned to
`EngineType.ONNXRUNTIME`, so this was never a live fault in this app; but a yanked floor
is still a floor a fresh resolve has to reach past deliberately, so it went to `>=3.9.2`
(latest, published 2026-07-21). Only `rapidocr` moved in the lock — 147 packages resolved,
one line changed, dependency list identical, so the `opencv-python ; sys_platform == 'never'`
override still does its job.

**Then the photo OCR test skipped.** Not failed — skipped, with
`rapidocr engine unavailable: Invalid OCR configuration.`

**Grading: confirmed**, by installing both versions side by side and constructing the engine
with this app's exact params dict: 3.8.2 builds it, 3.9.2 raises `ValueError`. Not a
platform quirk, not a flaky download — a genuine breaking change.

The cause is a default that moved under a partially-pinned config. `get_engine()` pinned
`Det.ocr_version` to PP-OCRv5 but left `Det.model_type` implicit, and 3.9.x changed the
Det/Rec defaults from `mobile`/PP-OCRv4 to `small`/PP-OCRv6. rapidocr selects a model from
the `(engine_type, ocr_version, lang_type, model_type)` tuple per stage; PP-OCRv5-det-`small`
has no entry in its model list, so construction raised — on **every photo OCR call**, not
just in tests.

The fix is not "add the one missing pin". It is to pin every axis that selects a model, per
stage, including the ones that happen to equal today's default — because the failure mode
is precisely a default changing while our explicit pins hold it in an impossible
combination. Twelve pins for three stages now, and a bump can only fail loudly at a pin
we wrote rather than quietly select weights nobody chose. Verified end to end: engine
constructs, and the round-trip test reads `factuur`/`rekening` back out of a real JPEG.

## 2. Why nobody would have noticed

The old guard:

```python
try:
    photo.get_engine()
except Exception as exc:  # model download / init failure -> skip
    pytest.skip(f"rapidocr engine unavailable: {exc}")
```

`except Exception` cannot tell a blocked model hub from a `ValueError` that means the
pipeline is dead. Both become a skip; pytest reports a skip as success. So the honest
counterfactual for landing the bump alone is: **CI green, 1370 passed, zero failures, and
photo OCR raising in production on every camera upload.** The file's own docstring
meanwhile claimed these tests "are required to pass in CI".

Narrowed to what is actually environmental:

- `OSError` — socket/DNS/TLS/filesystem, and by subclass `TimeoutError` and `requests`' errors
- `DownloadFileException` — rapidocr's download wrapper, a bare `Exception` subclass, so it
  needs naming explicitly

Everything else propagates: `ImportError` for a removed export, `AttributeError` for a
renamed enum, `TypeError` for a changed signature, `ValueError` for a rejected params dict.
`require_tesseract_stack()` was left alone — `shutil.which` is already a precise capability
probe, and it is the pattern the new `require_rapidocr_engine()` copies.

One trap worth recording. The obvious meta-test is
`with pytest.raises(TypeError): require_rapidocr_engine()`, and it is **useless**: if the
guard regresses and skips, `Skipped` propagates straight through `pytest.raises` and marks
the meta-test *skipped* rather than failed. The one test written to catch the regression
would have been invisible in exactly the case it exists for. So it catches `Skipped` first
and converts it to `pytest.fail`. Confirmed by mutation — restoring `except Exception` gives
**5 failed**, not 5 skipped.

## 3. The floor, because a skip still reads as success

Narrowing the guard fixes the wrong-exception case but leaves the shape intact: a real
model-hub outage in CI still skips, and a skip is still green. So the backend job now runs
`scripts/check_engine_skips.py` over `--junitxml` output and fails when any test skipped for
a rapidocr **or** tesseract reason. CI installs the tesseract stack and has network for the
model hub, so neither guard has a legitimate reason to fire there; if one does, that is a
broken engine or broken provisioning, and either way not a passing build.

It matches on the skip *reason* rather than test ids, so renaming or moving a test does not
blind it, and a new test borrowing the same guard is covered for free. Deliberate details:

- **Missing or unparseable report exits 2, an offending skip exits 1.** "Nothing found" and
  "I could not look" must not share an exit code — a scanner that matches nothing passes
  loudest when it is blind.
- **A `DOCTYPE` is refused outright.** A pytest JUnit report never has one, so this costs
  nothing and removes entity expansion as a possibility rather than arguing about whether
  it is reachable. (Stdlib `ElementTree` already rejects *external* entity references —
  checked, not assumed — which is the file-disclosure half; this covers the internal half.)
  No new dependency for a script that parses its own output.

Written as a real script under `scripts/` with 14 tests, one per hole, rather than a heredoc
in the workflow YAML — nothing lints or types code embedded in YAML, and the repo's ruff
run covers `scripts/`.

Both directions are verified against real pytest output, not just unit-mocked: engines
present → `exit=0`; a simulated offline hub → the test skips with the underlying message
(so the guard's environmental contract holds) and the floor exits 1 naming the test.

## 4. Result

1391 passed, **0 skipped**, coverage 95% against a 93% gate. The +21 over the previous 1370
is 7 guard tests and 14 for the new script.

The transferable bit is not about OCR. A `try/except Exception → skip` around a dependency's
entry point is a **silent-failure generator with a delay fuse**: it costs nothing until the
dependency changes, and it pays out at precisely the moment you most need to be told. This
one was armed for as long as the pin held still.

# Matter classifier: reclassify mode + precision tuning

Follow-up to the [business-matter categorization](260717-business-matter-categorization.md)
feature shipped and deployed the same day. Live use surfaced a real accuracy
problem: "Car insurance" was catching documents that are car-*related* but not
insurance (purchases, fines, servicing). Three changes to make the classifier's
predictions tunable and sharper.

## The core insight

Two separate problems were conflated in "the AI predicts badly":

1. **Loose decisions** — nothing told the model that a fine mentioning a car is
   not car insurance.
2. **Sticky mistakes** — the classifier was **merge-only** (add, never remove, to
   protect manual edits), so improving hints and re-sweeping would *add* correct
   matters but never *remove* the wrong ones already sitting there. The obvious
   fix (tune hints, re-run sweep) would visibly fail to clean up the mess.

Fixing (1) without (2) would have frustrated the user, so both shipped together.

## What changed

1. **`sweep-matters --reclassify` (replace mode).** `apply_matter_classification`
   gained a `replace` flag: instead of merging, it sets the document's matters to
   exactly the fresh prediction. The user-edited guard still runs *first*, so
   replace only ever touches auto-assigned memberships — hand curation is never
   clobbered. Threaded through the `classify_document_matters` job
   (`replace` kwarg, default False so ingest stays add-only) and the CLI
   (`--reclassify` implies the whole corpus in replace mode). This makes the
   tuning loop work: *sharpen a hint → `sweep-matters --reclassify` → inspect →
   repeat*.
2. **Precision prompt (v2).** Rewrote `SYSTEM_PROMPT` to match the document's
   *primary subject* not incidental mentions, honour a hint's exclusions, bias
   toward precision (leave unfiled when unsure), and use the sender as a signal.
   Bumped `PROMPT_VERSION` to `matter-classifier-v2`.
3. **Model → sonnet.** `matter_classifier_model` default `claude-haiku-4-5` →
   `claude-sonnet-4-6`: the "related-but-not" judgement rewards nuance and the
   call is infrequent. Bumped the daily budget \$1 → \$3 for headroom during
   iterative reclassify sweeps on the pricier model.

Provenance now records `mode` (merge/replace) and `removed_slugs` alongside the
existing fields.

## Verification (observed)

- Backend `uv run pytest` → **1311 passed** (+5: three replace-mode classifier
  tests incl. empty-prediction-clears-all and replace-still-skips-user-edited, two
  CLI tests for `--reclassify` replace-mode job args vs default merge mode);
  `ruff check .` + `ruff format --check .` clean.
- No frontend changes this cycle.

## What is deliberately not done

1. **No automatic reclassify.** `--reclassify` is a deliberate manual operation —
   auto-replacing on every vocabulary edit would be surprising and could churn
   spend. The operator decides when to re-file.
2. **No per-match confidence threshold.** Considered having the model return a
   confidence per match and attaching only above a bar; the precision-biased
   prompt is the lighter lever and was tried first. Revisit if precision is still
   short after hint tuning.
3. **Merge remains the ingest default.** New uploads still add-only; replace is
   reserved for the operator-driven sweep, so a routine re-ingest can never wipe
   an auto-membership out from under the user.

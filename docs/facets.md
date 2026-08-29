# Facet vocabulary

**Status:** active. **Last updated:** 2026-08-29 (the `category` facet grew 4 values — `vehicle-purchase`, `dining`, `employment`, `equipment-certification` — after a full labelling run over 258 real documents left 8 uncategorised and the model's own suggestion queue named all four; §3's description of value/alias matching corrected to say casefolded (case-insensitive), not exact-match, since `parse_label_response` now casefolds — and corrected again same day after a review caught the first correction overclaiming accent-insensitivity too: casefold does not strip diacritics, so `Škoda` and `Skoda` still do not match one another.) Earlier (2026-08-28): (initial version: the closed-set facet vocabulary that replaces free-form tags for the axes charts and search need — `category`, `scope`, `cost_type`, plus `vehicle`/`property`/`person`, which ship empty. Design: [superpowers/specs/2026-08-28-charts-redesign-design.md](superpowers/specs/2026-08-28-charts-redesign-design.md) §6–7.5, plan: [superpowers/plans/2026-08-28-charts-facet-vocabulary.md](superpowers/plans/2026-08-28-charts-facet-vocabulary.md)).
**Last verified:** 2026-08-29 — method: diffed the new `category` row (19 values, order and keys) and §3's matching description against `SEED_VOCABULARY` in `src/library/facets/seed.py` and `parse_label_response` in `src/library/facets/labeller.py` — the four new keys/labels/aliases match `seed.py` exactly. §3's matching prose was re-checked a second time same day after a review caught it overclaiming accent-insensitivity: confirmed by executing `'Skoda'.casefold() == 'Škoda'.casefold()` (`False`) and `'SKODA'.casefold() == 'Škoda'.casefold()` (`False`) that `str.casefold()` folds case only, not diacritics, so the doc now says case-insensitive and gives a same-script (`Skoda`/`SKODA`/`skoda`) example rather than a cross-accent one; covered by executed assertions in `tests/test_facet_labeller.py` (`test_an_alias_resolves_regardless_of_case_including_non_ascii_letters`, `test_a_value_key_differing_only_in_case_resolves`, `test_casefold_does_not_fold_diacritics`) and `tests/test_facet_seed.py`. Earlier (2026-08-28) — method: re-verified after a fix wave. §3 now describes the sanitisation `accept` applies to the key it derives and its 422, and §4 the 409 on merging a value into itself — both diffed against `derive_value_key`/`accept_suggestion` in `src/library/api/facets.py` and `merge_values` in `src/library/facets/vocabulary.py`, and covered by executed assertions in `tests/test_api_facets.py` and `tests/test_facet_crud.py`. §5's `--only` sentence is corrected (it bypasses the relabel skip-check entirely; `--relabel`/`--limit` do nothing alongside it) against `label_archive` in `src/library/cli.py`, and carries a new warning that `PATCH /api/admin/recipients/{id}` with `merge=true` (`rename_recipient` in `src/library/taxonomy.py`, read in full) drops the losing recipient's `user_id` link — an unfixed follow-up, not a claim about this branch's CLI path. Original method: read every module in `src/library/facets/` (`vocabulary.py`, `seed.py`, `labeller.py`, `apply.py`, `backfill.py`, `recipients.py`) and `src/library/api/facets.py` in full; the `Facet`/`FacetValue`/`FacetValueAlias`/`DocumentLabel`/`FacetValueSuggestion` models and the `document_labels`/`facet_values` constraints in `src/library/models.py`; the `facet` query parameter and its 422 paths in `src/library/api/documents.py` and `src/library/search.py`; the `label-archive` and `recipients` commands in `src/library/cli.py`; and the wire-behaviour assertions in `tests/test_api_facets.py`, `tests/test_facet_search.py`, `tests/test_facet_crud.py` and `tests/test_facet_backfill.py`. No tests were executed as part of writing this document; a full `uv run pytest -q` run is recorded in the journal entry for this work.

## 1. What a facet is, and why it is not a tag

A **facet** is one closed dimension of classification — `category`, `scope`,
`cost_type` — and a document carries **at most one value per facet**. A
**tag** is an open, free-form string a document can carry any number of, with
no dimension attached.

The archive shipped with 771 distinct tags, 454 of them used exactly once.
Four things drove that: several spellings of the same concept living as
separate tags, encoding/spelling variants of the same word, unrelated axes
(place, person, year, scope, vendor) sharing one flat namespace, and tags that
duplicate a column the archive already has (`document_date`, `sender`,
`kind`). A free-form field has no way to stop any of that — every new tag is a
new possible synonym for an old one. A facet does, because a document can
only choose from a fixed list of values it was given: there is no field to
type a fifth spelling of "vehicle service" into.

The 771 tags were not migrated into the new vocabulary. They were read as
**evidence of which dimensions matter** (the design principle: tags inform
the vocabulary, documents determine the labels) and then discarded — mapping
a corrupt tag onto a clean-looking value would just launder the old drift
into the new system. Every document is labelled fresh, from its own title,
summary, sender, kind, amount and OCR text, against the closed vocabulary
below.

## 2. The shipped vocabulary

Seeded by `library.facets.seed.seed_vocabulary`, which the `label-archive`
CLI command runs automatically before it labels anything. Seeding is
additive and idempotent: it only ever creates a facet or value that is
missing, so it is safe to run again after adding a facet by hand, and it
never touches a value an owner has since renamed.

| facet | values |
| --- | --- |
| `category` | 19 values: accountancy, tax, vehicle-service, ev-charging, insurance, healthcare, software, energy, water, housing, parking, fines, pension, banking, travel, vehicle-purchase, dining, employment, equipment-certification |
| `scope` | business, personal |
| `cost_type` | subscription, usage, one-off |
| `vehicle` | *(ships with no values)* |
| `property` | *(ships with no values)* |
| `person` | *(ships with no values)* |

`vehicle`, `property` and `person` are created as facets with **zero
values**. A value in any of those three would have to name a real vehicle
registration, a real address, or a real person — and this repository is
public. Those values are created at runtime (`POST /api/facets/{key}/values`,
or `library`'s administration surface), once, on a live instance, and never
committed here. Several `category` values carry aliases (e.g. `accountancy`
also matches "accounting", "bookkeeping", "fiscal services") so the closed-set
match in §3 recognises a document's own wording without a human pre-cleaning
it.

`person` exists as a facet even though a `recipients` table already exists,
because they answer different questions: `recipient` is who a document was
*addressed to*; `person` is *whose cost it is*. A household's costs and
recipient addressing diverge often enough — one member addressed on most
household mail, a second member named only in body text on a handful of
documents — that collapsing the two would lose information neither `sender`
nor `recipient` can recover.

## 3. The closed-set rule and the suggestion queue

The labeller (`library.facets.labeller`) sends the model the full vocabulary
— every facet, every value, every alias — inside the prompt and asks for at
most one value per facet, by key, plus a confidence and a short reason. The
model is never shown a way to invent a value: `parse_label_response` looks up
whatever the model returns against the facet's known values and aliases,
case-insensitively (casefolded, so `Skoda`, `SKODA` and `skoda` all resolve
to a value keyed `skoda` without needing a separate alias per casing
variant — only the comparison folds case, never the stored key/alias/label
text itself). Casefolding does not fold diacritics: `Škoda` is a distinct
string from `Skoda` under this comparison, so a value whose canonical
spelling carries an accent (a real `vehicle` value, e.g. a marque like
`Škoda` — §2 covers why those values are never committed here) still needs
its unaccented spelling listed as a separate alias if the model might emit
it, alongside its casing variants. Anything that still matches nothing
becomes `value: null` plus a *suggested label* rather than a fabricated key.
That mapping is pure — no model call — so the closed-set guarantee is
unit-tested without one.

`library.facets.apply.apply_proposals` (the only module in this package that
both calls the model and writes to the database) then does three things per
facet, and the three-way split is the point:

- a proposal at or above `settings.facet_label_min_confidence` (default 0.6)
  with a resolved value is **applied** — written as a label;
- a proposal below the confidence floor, or with no resolved value, is
  **withheld** and the facet is reported unlabelled — a document is never
  guessed at;
- a value the model wanted but the vocabulary does not contain is queued as a
  **pending suggestion** (`facet_value_suggestions`), one row per
  `(facet, document, suggested_label)`.

`POST /api/facet-suggestions/{id}/accept` is the **only** sanctioned path
that widens the vocabulary. It derives a value key from the suggested label,
creates the value, and labels the originating document with it in the same
call — and answers `409` if that derived key already exists, whether checked
ahead of the insert or caught as a race between two concurrent accepts.
Because this is the only route that widens the set, the derived key is held
to the same contract `POST /api/facets/{key}/values` enforces: lower-cased,
spaces to hyphens, anything outside `[a-z0-9_-]` dropped, runs of `-`/`_`
collapsed, trimmed to 64 characters (`"EV charging (home)!"` →
`ev-charging-home`); the label itself is stored unchanged as the value's
display text. A label leaving nothing usable is a `422`, not a value with an
unusable key. `POST
/api/facet-suggestions/{id}/dismiss` rejects a suggestion without creating
anything. Every other write path in `src/library/facets/vocabulary.py`
refuses to create a value implicitly: naming a value that is not already in
the facet raises `UnknownValueError`, which the API turns into a `404` (on a
facet-value path) or a `422` (on a document label, since the value came from
a client's request body — see [api.md](api.md)).

## 4. Vocabulary edits, and what each one costs

| operation | cost |
| --- | --- |
| rename a value's display label | free — labels reference `facet_value_id`, never the display text |
| add an alias | free |
| merge two values | cheap — repoints every label from the source to the target in one `UPDATE`, keeps the source key as an alias of the target |
| create a facet or a value | free to create; a labelling pass is needed before any document actually carries it |
| split one value into two | **not an operation** — there is no `split` call. It is `create_value` for the new value, followed by re-labelling the affected documents (`library label-archive --relabel`, or `--only <id>` per document) so the model re-decides which of the two each one belongs to |
| delete a value | blocked (`409`) while any document still carries it |

Rename and alias are free because nothing in the schema stores a value's
text anywhere except `facet_values.label` itself — every label row and every
search filter addresses a value by its `facet_value_id`, so changing the
display string moves nothing. Merge is a bulk repoint (`UPDATE
document_labels SET facet_value_id = :into WHERE facet_value_id = :from`)
plus folding the merged-away value's aliases onto the survivor, so it costs
one query regardless of how many documents carry the value — `POST
/api/facets/{key}/values/{value}/merge` also accepts `dry_run: true`, which
runs the same count the real merge would move and returns it without writing
anything, so an owner can preview a merge before committing to it. Merging a
value into itself is refused with a `409` (real run and dry run alike): the
fold is a copy-then-delete, so with both sides equal it would delete the
value and every alias it had.

Split has no dedicated call because there is nothing mechanical to do: the
system does not know which of the new two values each existing document
belongs to, only a model re-reading each document's content can decide that.

## 5. `library label-archive`

```bash
library label-archive                  # seed the vocabulary if needed, label every unlabelled document
library label-archive --limit 50       # stop after 50 documents
library label-archive --only 412       # label (or re-label) just document 412
library label-archive --relabel        # also re-label documents that already carry labels
```

Seeds any missing vocabulary rows first (idempotent, see §2), then selects
documents to label: without `--relabel`, a document carrying **any** existing
label is skipped, which is what makes the command safe to re-run — after
adding a facet or a value, re-running it only reaches documents that have
never been touched. `--relabel` reconsiders every document against the
current vocabulary, and `--limit` caps how many documents a run touches.
`--only <id>` is a separate path: it labels exactly that document, always,
bypassing the skip-check above — so `--relabel` and `--limit` have no effect
alongside it.

Each document commits on its own, so a run interrupted partway through
leaves everything already labelled in place — the command is safe to
re-invoke rather than needing a rollback. The same labelling path
(`library.facets.apply.label_and_apply`) also runs automatically at the end
of extraction for every newly ingested document, best-effort: a missing API
key or an unparseable model response leaves a document unlabelled rather than
failing ingestion.

`library recipients --list` and `library recipients --merge
KEEP_ID:DROP_ID[,DROP_ID...]` live in the same package
(`library.facets.recipients`) for the same reason facets exist: the
`recipients` table had the identical drift as the tags — several rows
spelling one person's name several ways. `--list` groups recipients whose
names normalise alike (lower-cased, punctuation stripped, whitespace
collapsed) and shows each group's document count; `--merge` repoints every
document from the drop ids onto the keep id and deletes the drop rows. A
recipient can carry a `user_id` link (auto-created when a user's display name
matches a recipient); the merge transfers that link onto the survivor when
exactly one unambiguous link exists among the ids being merged, and refuses —
naming the conflicting recipient ids, moving nothing — when the keep id and
the drop ids disagree about which user is linked. This is a distinct, CLI-only
bulk tool; the interactive per-recipient rename/merge admins do day to day is
still `PATCH /api/admin/recipients/{id}` (see [admin.md](admin.md)) — but
**prefer `library recipients --merge` for anything carrying a `user_id`
link**: `PATCH .../recipients/{id}` with `merge=true`
(`library.taxonomy.rename_recipient`) has the `user_id`-loss bug the CLI path
fixes — it deletes the losing recipient without transferring the link and
without refusing on a conflict. Tracked as a follow-up; unchanged here.

## 6. REST surface

The full wire contract — every route, status code and JSON shape — is in
[api.md](api.md); this is the shape of it. `GET /api/facets` returns the
whole vocabulary in one call (a few dozen rows; every facet, value and
alias). `POST /api/facets` and `POST /api/facets/{key}/values` create a facet
or a value. `PATCH .../values/{value}` renames a label; `POST
.../values/{value}/aliases` adds an alias; `POST .../values/{value}/merge`
folds one value into another (with `dry_run`); `DELETE .../values/{value}`
removes an unused value. `GET`/`PUT /api/documents/{id}/labels` read and set
one document's labels — a label is not embedded in the document list or
detail response, so a caller that needs it fetches it separately.
`GET /api/facet-suggestions` and the `accept`/`dismiss` actions on
`/api/facet-suggestions/{id}` work the pending queue from §3. Documents are
filterable by facet with a repeatable `?facet=key:value` query parameter on
`GET /api/documents`, AND-composed with every other filter.

## 7. `parent_id` — reserved, not used

`facet_values.parent_id` is a nullable, self-referencing foreign key on
`facet_values`, present in the schema since the first migration and unused by
every module in `src/library/facets/` and every route in
`src/library/api/facets.py` today — every value returned by `GET /api/facets`
carries `parent_id: null`. It exists so that if a facet ever needs a second
level (a `category` value that should itself have sub-values, say) that can
be added as a **data change** — populate `parent_id` on the existing rows —
rather than a schema migration.

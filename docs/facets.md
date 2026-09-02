# Facet vocabulary

**Status:** active. **Last updated:** 2026-09-02 (§6 gains a paragraph: the vocabulary is now reachable from Ask (#136) — both tools take a `facets` argument and the archive-context block carries the keys with their allowed values. It also states the scope difference that would otherwise be found the hard way: an Ask filter matches a **document's** labels, where a chart splits on `spend_facts` row labels and can therefore distinguish a split document's lines.) Earlier: 2026-09-01 (§3's key-derivation paragraph now records that `accept_suggestion` transliterates accents rather than dropping them — `Škoda` derives `skoda`, not `koda` — and states explicitly that this changes derivation only, never the accent-sensitive matching rule described earlier in the same section (#113). new §2.2 documents when the per-document editor's draft is kept and when it is replaced: a label fetch that resolves after the owner has already chosen a value no longer clobbers the selection, which used to leave Save disabled permanently with no error shown (#144). Earlier the same day: new §2.1 documents where a facet renders and in what order: the filter bar now offers a facet only once it has two or more values — a one-option select partitions nothing, since every document it can show carries the same value — while the per-document editor still renders every facet, empty ones disabled. Both pickers now sort a facet's values by label rather than by the stored `ordinal`, which is seed-insertion order; the sort is in the components, not in `load_vocabulary`, so the server's canonical order and the LLM labelling prompt are unchanged. Nothing is deleted to hide a facet — §2.1 says why, and §4's 409 is the reason.) Earlier (2026-08-30): (final fix wave: §8's alias-refusal quote corrected — it read "already an alias", a paraphrase that does not appear anywhere in the code; the string `FacetsPanel.vue`'s `saveAlias` actually renders is `Already covered by the alias '<x>' — aliases match case-insensitively.`, and §8 now quotes that verbatim.) Earlier the same day (facet vocabulary panel, Task 11: new §8 documents the `/vocabulary` panel — its three tabs, the target-specific merge preview, the verbatim delete-refusal reason, the validated six-slot palette with null-means-derived colour, and that a new facet carries no documents until `library label-archive` runs; §4's cost table gains a row pointing every operation at the panel; §6 gains a paragraph on `GET /api/facets/label-counts` and how it differs from `/api/facets/counts`. See [api.md](api.md) §1.23.6 for the wire contract. Earlier the same day (spending-view backend, Task 8: new §4.1 documents a value's optional stored `colour` (migration 0037) — null is the normal state and means the client derives a palette slot from the value's `key`; set or cleared through `PATCH /api/facets/{facet_key}/values/{value_key}`, independently of `label`, told apart by presence in the request body rather than by value; a new row in §4's cost table; §6 updated to say `GET /api/facets` carries `colour` and the value-PATCH route edits it. See [charts.md](charts.md) §4 and §11 for the split axis this exists for, and [api.md](api.md) §1.23/§1.8.4.0 for the full wire contract on both the facet-value and sender routes.)) Earlier (2026-08-29): the `category` facet grew 4 values — `vehicle-purchase`, `dining`, `employment`, `equipment-certification` — after a full labelling run over 258 real documents left 8 uncategorised and the model's own suggestion queue named all four; §3's description of value/alias matching corrected to say casefolded (case-insensitive), not exact-match, since `parse_label_response` now casefolds — and corrected again same day after a review caught the first correction overclaiming accent-insensitivity too: casefold does not strip diacritics, so `Škoda` and `Skoda` still do not match one another.) Earlier (2026-08-28): (initial version: the closed-set facet vocabulary that replaces free-form tags for the axes charts and search need — `category`, `scope`, `cost_type`, plus `vehicle`/`property`/`person`, which ship empty. Design: [superpowers/specs/2026-08-28-charts-redesign-design.md](superpowers/specs/2026-08-28-charts-redesign-design.md) §6–7.5, plan: [superpowers/plans/2026-08-28-charts-facet-vocabulary.md](superpowers/plans/2026-08-28-charts-facet-vocabulary.md)).
**Last verified:** 2026-09-02 — method: partial, scoped to the new §6 paragraph, and split by claim because one observation cannot carry both halves. **Tools:** `_FILTER_PROPERTIES` is spread into both `semantic_search` and `query_documents` in `src/library/ask/engine.py`, and the forwarding was checked by execution, not by reading — a test asserts a facet-filtered `semantic_search` returns `coverage.matched == 1` where the unfiltered call returns 2, and it was observed failing (`assert 2 == 1`) with the `facets=` argument mutated out of `_filters_from_args`. **Prompt block:** `render_archive_context` in `src/library/ask/context.py` was read after the change and emits keys and value keys. **Scope difference:** `filter_conditions` in `src/library/search.py` builds its facet EXISTS against `document_labels`, while `charts/query.py`'s `_SPLIT_FACET` reads `sf.labels ->> :axis` — two different relations, which is what makes the paragraph's last sentence true rather than plausible. Nothing else on this page was re-checked. Earlier: 2026-09-01 — method: read `derive_value_key` in `src/library/api/facets.py` in full after editing it and diffed §3's derivation paragraph against it step by step (NFKD + combining-mark strip, then `.lower()`, then the space→hyphen replace, then `_DISALLOWED`, then `_REPEATED_SEPARATORS`, then the 64-character trim). Every claim in that paragraph is covered by an executed assertion in `tests/test_api_facets.py`: `test_derive_value_key_transliterates_rather_than_dropping_accents` (parametrized over `Škoda`→`skoda`, `Citroën`→`citroen`, `Ångström`→`angstrom`, a mixed-script label, and the unchanged `EV charging (home)!`→`ev-charging-home` case), `test_derive_value_key_still_returns_empty_when_nothing_transliterates` (Japanese, Greek, punctuation-only), `test_accepting_an_accented_suggestion_derives_a_readable_key` (end-to-end through the route, asserting the label keeps its diacritics) and `test_accepting_an_entirely_non_latin_suggestion_is_422`. The six discriminating cases were each run RED against the unmodified function before the fix (`koda` vs `skoda`, `citron` vs `citroen`) and green after. The 'matching is unchanged' claim was checked by running `tests/test_facet_labeller.py` green unmodified — including `test_casefold_does_not_fold_diacritics`, whose fixture deliberately keeps a legacy `koda` key so that it still proves the fall-through. Full backend suite green this pass (`uv run coverage run -m pytest` — 2090 passed, 7 skipped), with `ruff check`, `ruff format --check` and `mypy` clean. Nothing else in this document was re-checked this pass. read `FacetEditor.vue` in full after editing it, and read the two fetch call sites it depends on in `DocumentDetailView.vue` (`fetchFacets` in `onMounted`, `fetchDocumentLabels` in the route watcher) to confirm nothing orders them — that unordered pair is the whole basis of §2.2's claim. Diffed §2.2's table against the component's actual guards: the `touched` flag set in `onSelect`, the early return in the `props.labels` watch, and the two places it is cleared (before `emit('saved')`, and in the `props.documentId` watch). Every row of that table is covered by an executed assertion in `FacetEditor.spec.ts`: 'keeps the user selection when the label map arrives after it', 'still saves the selection that a late label map tried to clobber', 're-hydrates from the server after a save, rather than staying pinned to the draft' and "drops the previous document's draft when the document changes". The first two were run RED against the unmodified component (the select reverted to `''`; the PUT was never issued at all, which is the user-visible symptom) and green after; the latter two passed both before and after, which is what shows the fix did not over-correct into freezing the editor. Whole frontend suite green this pass (`npx vitest run` — 112 files, 1420 tests), with `npm run lint` and `npm run type-check` clean. §2.1's claims were re-read against the same file and are unchanged. Nothing else in this document was re-checked this pass. Earlier the same day — method: read `FacetFilterBar.vue` and `FacetEditor.vue` in full after editing them and diffed §2.1's table against their actual guards (`facet.values.length > 1` for the bar; the editor's `:disabled="facet.values.length === 0"` plus its "No values yet" hint). Every claim in §2.1 is covered by an executed assertion: `FacetFilterBar.spec.ts`'s 'omits a facet with only one value', 'renders a facet once it reaches two values' and 'lists a facet's values alphabetically by label', and `FacetEditor.spec.ts`'s 'lists a facet's values alphabetically by label' alongside the pre-existing 'renders an empty facet as a disabled select rather than hiding it'. The three discriminating ones were each run RED against the unmodified component before the fix (e.g. `['Any', 'Software', 'Energy']` vs `['Any', 'Energy', 'Software']`) and green after; the whole frontend suite ran green this pass (`npm run test:unit` — 112 files, 1416 tests), with `npm run lint` and `npm run type-check` clean. The 'nothing is deleted' claim rests on §4's existing delete-409 verification, not re-checked here, plus a direct read of the live vocabulary confirming the single-value facet's value carries a three-figure label count. `load_vocabulary`'s ordering was read, not changed. Nothing else in this document was re-checked this pass. Earlier (2026-08-30) — method: (final fix wave) re-read `saveAlias` in `frontend/src/views/vocabulary/FacetsPanel.vue` and confirmed the exact string it renders on a duplicate alias is `` `Already covered by the alias '${existingMatch}' — aliases match case-insensitively.` `` — §8's quote is corrected to match verbatim. That string is covered by an executed assertion in `FacetsPanel.spec.ts`'s `'refuses to add an alias the value already has ...'` test, run green as part of this pass's full `npm run test:unit`. Nothing else in this document was re-checked this pass. Earlier the same day — method: (facet vocabulary panel, Task 11) read `VocabularyView.vue`, `views/vocabulary/{FacetsPanel,SendersPanel,SuggestionsPanel,ValueMergeView}.vue`, `components/vocabulary/SplitColourPicker.vue`, `utils/{splitPalette,slugify}.ts` and `router/index.ts`'s two `vocabulary*` route entries in full, and diffed new §8 against them line by line. The target-specific merge-preview invalidation is `ValueMergeView.vue`'s `watch(target, ...)` (nulls `moved`/`previewFor` synchronously before the new dry run resolves) plus `canApply`'s `previewFor.value === target.value` clause; the four-part diff (only `moved` server-sourced) is its `gainedAliases`/`sharedAliases`/`losesColour` computed properties. The verbatim-409 claim is `FacetsPanel.vue`'s `err instanceof ApiError ? err.detail : ...` catch pattern on delete, cross-checked against the server string in `src/library/facets/vocabulary.py`'s `delete_value`. The case-insensitive alias pre-check with its diacritic carve-out is `FacetsPanel.vue`'s duplicate-alias comment and `.toLowerCase()` compare. The six-slot palette and null-default are `splitPalette.ts`'s `SPLIT_PALETTE`/`deriveSlot`/`resolveSplitColour`, and "no free hex field" was confirmed by reading the whole of `SplitColourPicker.vue` (a `<select>`-free swatch grid plus a Default button, no text input). All of it is covered by executed assertions, run green this pass: `cd frontend && npx vitest run splitPalette FacetsPanel ValueMergeView SendersPanel SuggestionsPanel VocabularyView slugify SplitColourPicker` — 8 files, 66 tests passed. The container-query claim (§8 doesn't restate it directly but §4's new panel row implies the layout works at all three e2e widths) carries forward [frontend.md](frontend.md)'s own verification of that same code, not re-tested here. Nothing else in this document was re-checked this pass; the rest carries forward its previous verification below unchanged. Earlier the same day — method: read the `colour` columns on `FacetValue` and `Sender` and their `CheckConstraint` in `src/library/models.py`, `migrations/versions/0037_split_colour.py` in full, `ValuePatch`/`Colour`/`patch_value` and the `colour` field on `ValueOut` in `src/library/api/facets.py`, and `set_value_colour`/`get_value` in `src/library/facets/vocabulary.py`. New §4.1's nullability, `model_fields_set` absent-vs-null distinction, and the CHECK/column-width design are covered by executed assertions in `tests/test_api_facets.py` (`test_get_facets_returns_colour`, `test_a_value_s_colour_can_be_set_without_renaming_it`, `test_an_explicit_null_clears_a_colour_and_an_absent_field_does_not`, `test_a_malformed_colour_is_a_422_not_a_500`) and `tests/test_split_colour.py`, run green as part of a full backend pass (see the journal entry for the count). §6's sentence on the sender route is covered by `test_get_senders_returns_colour`, `test_a_sender_s_colour_can_be_set_and_cleared` and `test_a_malformed_sender_colour_is_a_422`. Nothing else in this document was re-checked this pass; the rest carries forward its previous verification below unchanged. Earlier (2026-08-29) — method: diffed the new `category` row (19 values, order and keys) and §3's matching description against `SEED_VOCABULARY` in `src/library/facets/seed.py` and `parse_label_response` in `src/library/facets/labeller.py` — the four new keys/labels/aliases match `seed.py` exactly. §3's matching prose was re-checked a second time same day after a review caught it overclaiming accent-insensitivity: confirmed by executing `'Skoda'.casefold() == 'Škoda'.casefold()` (`False`) and `'SKODA'.casefold() == 'Škoda'.casefold()` (`False`) that `str.casefold()` folds case only, not diacritics, so the doc now says case-insensitive and gives a same-script (`Skoda`/`SKODA`/`skoda`) example rather than a cross-accent one; covered by executed assertions in `tests/test_facet_labeller.py` (`test_an_alias_resolves_regardless_of_case_including_non_ascii_letters`, `test_a_value_key_differing_only_in_case_resolves`, `test_casefold_does_not_fold_diacritics`) and `tests/test_facet_seed.py`. Earlier (2026-08-28) — method: re-verified after a fix wave. §3 now describes the sanitisation `accept` applies to the key it derives and its 422, and §4 the 409 on merging a value into itself — both diffed against `derive_value_key`/`accept_suggestion` in `src/library/api/facets.py` and `merge_values` in `src/library/facets/vocabulary.py`, and covered by executed assertions in `tests/test_api_facets.py` and `tests/test_facet_crud.py`. §5's `--only` sentence is corrected (it bypasses the relabel skip-check entirely; `--relabel`/`--limit` do nothing alongside it) against `label_archive` in `src/library/cli.py`, and carries a new warning that `PATCH /api/admin/recipients/{id}` with `merge=true` (`rename_recipient` in `src/library/taxonomy.py`, read in full) drops the losing recipient's `user_id` link — an unfixed follow-up, not a claim about this branch's CLI path. Original method: read every module in `src/library/facets/` (`vocabulary.py`, `seed.py`, `labeller.py`, `apply.py`, `backfill.py`, `recipients.py`) and `src/library/api/facets.py` in full; the `Facet`/`FacetValue`/`FacetValueAlias`/`DocumentLabel`/`FacetValueSuggestion` models and the `document_labels`/`facet_values` constraints in `src/library/models.py`; the `facet` query parameter and its 422 paths in `src/library/api/documents.py` and `src/library/search.py`; the `label-archive` and `recipients` commands in `src/library/cli.py`; and the wire-behaviour assertions in `tests/test_api_facets.py`, `tests/test_facet_search.py`, `tests/test_facet_crud.py` and `tests/test_facet_backfill.py`. No tests were executed as part of writing this document; a full `uv run pytest -q` run is recorded in the journal entry for this work.

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

### 2.1 Where a facet renders, and in what order

The two facet pickers apply deliberately opposite visibility rules, because
they answer opposite questions.

| surface | shows | rule |
| --- | --- | --- |
| `FacetFilterBar.vue` — the document list's filter row | facets with **two or more** values | a filter you cannot use to compare anything is noise |
| `FacetEditor.vue` — a document's Facets card | **every** facet, empty ones disabled with a "No values yet" hint | the owner must see a facet exists before asking for a value in it |

The filter bar's threshold is two, not one. An empty select is obviously
useless, but a one-option select is barely better: every document it can show
carries the same value, so choosing it partitions nothing the owner wanted
partitioned. On a real archive this is not hypothetical — `property` and
`vehicle` each sit at one or two values for long stretches, and `property` in
particular tends to hold a single address for years. The rule is keyed on the
value **count**, never on a facet key, so a facet that grows a second value
reappears in the bar with no code change.

Nothing is deleted to achieve this. A single-value facet's value typically
carries a large share of the archive's labels, and `DELETE
/api/facets/{facet_key}/values/{value_key}` refuses with a 409 naming that
count (§4). Hiding is a display decision; the labels stay, `?facet=` still
filters on them, and the `/vocabulary` panel still manages them.

### 2.2 The editor's draft outlives a late fetch

`FacetEditor.vue` holds the owner's in-progress selection in a local draft and
enables **Save labels** only while that draft differs from the last-saved label
map it was handed. Two independent things can replace that label map, and the
draft must survive one of them and follow the other:

| event | draft |
| --- | --- |
| the document's labels arrive (or re-arrive) from the server | **kept**, if the owner has already touched it |
| a save round-trips, or the page switches to another document | **replaced** — the server is the truth again |

Keeping it matters because `DocumentDetailView.vue` feeds this component from
two unordered fetches: the vocabulary from `fetchFacets` on mount, and the
document's own labels from `fetchDocumentLabels` in the route watcher. The
selects become usable as soon as the *vocabulary* lands, so on a slow backend
the owner can pick a value before the *labels* have arrived. A draft that
re-hydrated unconditionally then reset itself to the server's (usually empty)
map: the selection vanished, and since Save is disabled while nothing differs,
the button went dead permanently with nothing on screen explaining why. Only
the label map is late — the diff that the `PUT` sends is computed against
whatever map is current, so a late arrival is still accounted for.

The same guarantee covers a failed save (§4): a rejected `PUT` leaves the edit
in the draft and shows an error. In both cases the rule is that an edit is
never discarded without saying so.

Both pickers order a facet's values **by label**, not by the stored `ordinal`
that `load_vocabulary` returns them in. That ordinal is seed-insertion order:
useful to the `/vocabulary` panel, which exists to expose it, and unusable in
a dropdown of `category`'s nineteen entries. The sort is done in the two
components rather than in `load_vocabulary` on purpose — the server's order
also fixes the order values are listed in the LLM labelling prompt (§3), and
reordering that is a change to labelling behaviour, not to presentation.

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
to the same contract `POST /api/facets/{key}/values` enforces: accents folded
to their base letters, lower-cased, spaces to hyphens, anything still outside
`[a-z0-9_-]` dropped, runs of `-`/`_` collapsed, trimmed to 64 characters
(`"EV charging (home)!"` → `ev-charging-home`, `"Škoda"` → `skoda`,
`"Citroën"` → `citroen`); the label itself is stored unchanged as the value's
display text, diacritics and all. A label leaving nothing usable is a `422`,
not a value with an unusable key.

The accent-folding step is NFKD normalisation with the combining marks
dropped, and it runs **before** the `[a-z0-9_-]` filter — which is the whole
point. Filtering an accented label directly deletes the letter rather than
replacing it, so `Škoda` derived `koda` and `Citroën` derived `citron`, a
different word. That mattered here more than anywhere else: a value's *label*
can be renamed at any time, but its *key* is the stable identifier every rule
and every stored `document_labels` row references, so a mangled key was
effectively permanent once accepted. A label with no Latin form at all
(entirely Greek or Japanese, say) still folds to nothing and is still a `422`
— transliteration must not rescue a label into some arbitrary key.

Note this is key **derivation**, and it does not change value **matching**,
which stays accent-sensitive by design (above): folding the key does not add
`Skoda` as an alias of `Škoda`, and an accented canonical spelling still needs
its unaccented form listed explicitly. Keys already in an archive are not
rewritten either, so a value carrying a pre-existing mangled key keeps it. `POST
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
| set or clear a value's stored colour | free — a display-only override on the value row itself, independent of the label |
| add an alias | free |
| merge two values | cheap — repoints every label from the source to the target in one `UPDATE`, keeps the source key as an alias of the target |
| create a facet or a value | free to create; a labelling pass is needed before any document actually carries it |
| split one value into two | **not an operation** — there is no `split` call. It is `create_value` for the new value, followed by re-labelling the affected documents (`library label-archive --relabel`, or `--only <id>` per document) so the model re-decides which of the two each one belongs to |
| delete a value | blocked (`409`) while any document still carries it |
| do any of the above from the UI | the vocabulary panel, `/vocabulary` (§8) — every operation in this table now has a client; before this, only the CLI and raw HTTP could reach it |

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

### 4.1 A value's colour

Every value carries an optional `colour` (migration 0037): a stored six-digit
`#rrggbb` hex string, or `null`. **Null is the normal state**, not an unset
default waiting to be filled in — it means the client derives a stable
palette slot from the value's `key` instead of reading a stored one, which is
what lets a chart's legend be coloured consistently before anyone has picked
a colour by hand. Setting one is how an owner overrides that derived slot for
a value they want to stand out or match an external convention (e.g. a
vehicle's own brand colour, once `vehicle` carries values).

Set or cleared through `PATCH /api/facets/{facet_key}/values/{value_key}`
(`{"colour": "#rrggbb"}` or `{"colour": null}`), independently of the label —
an **absent** `colour` in the request body leaves the stored value untouched,
while an **explicit `null`** clears it back to the derived slot; the two are
told apart by whether the key is present in the request body at all, not by
its value, since `null` must mean something different from "not sent" (see
[api.md](api.md) §1.23). The same optional-`colour`-with-the-same-nullability
shape exists on `senders` (`PATCH /api/senders/{id}`, api.md §1.8.4.0), since
`split=sender` is a chart split axis exactly like a facet ([charts.md](charts.md)
§4) and needs the same stable-legend-colour treatment; a sender is not part of
this vocabulary and has no label to go with the colour, only a name.

The format is enforced by an explicit `CHECK (colour ~ '^#[0-9a-fA-F]{6}$')`
in the database, not only by the API's own pattern validation: the column
itself is a plain `String(32)`, deliberately wider than a hex value needs, so
the CHECK is the sole judge of format on any writer that is not this API (a
future admin script, a data migration) — a tightly-sized column would refuse
an over-length value as a Postgres `DataError` rather than the `IntegrityError`
this schema's other constraints raise, a second and differently-shaped
enforcer.

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
alias, plus each value's stored `colour` — §4.1). `POST /api/facets` and
`POST /api/facets/{key}/values` create a facet or a value. `PATCH
.../values/{value}` edits a value's label and/or its `colour` (§4.1),
independently of each other; `POST
.../values/{value}/aliases` adds an alias; `POST .../values/{value}/merge`
folds one value into another (with `dry_run`); `DELETE .../values/{value}`
removes an unused value. `GET`/`PUT /api/documents/{id}/labels` read and set
one document's labels — a label is not embedded in the document list or
detail response, so a caller that needs it fetches it separately.
`GET /api/facet-suggestions` and the `accept`/`dismiss` actions on
`/api/facet-suggestions/{id}` work the pending queue from §3. Documents are
filterable by facet with a repeatable `?facet=key:value` query parameter on
`GET /api/documents`, AND-composed with every other filter.

**The vocabulary is also reachable from Ask** (#136). Both Ask tools take a
`facets` object argument, and the archive-context block in Ask's system prompt
carries the facet keys with their allowed value keys, so the model can name a
filter rather than approximate a category by sender or kind. Note the scope
differs from a chart's: an Ask filter matches a **document's** own labels, where
a chart splits on the labels of `spend_facts` rows and so can distinguish the
lines of a split document. See [ask.md](ask.md) §1.2.

`GET /api/facets/label-counts` sits beside `GET /api/facets/counts` and
answers a different question: it counts rows in `document_labels` directly,
unfiltered, so it includes every value a document carries whether or not
that document has an amount — the number the vocabulary panel (§8) shows and
`DELETE .../values/{value}` enforces, not the number `/api/facets/counts`
proposes charts from ([api.md](api.md) §1.23.6).

## 7. `parent_id` — reserved, not used

`facet_values.parent_id` is a nullable, self-referencing foreign key on
`facet_values`, present in the schema since the first migration and unused by
every module in `src/library/facets/` and every route in
`src/library/api/facets.py` today — every value returned by `GET /api/facets`
carries `parent_id: null`. It exists so that if a facet ever needs a second
level (a `category` value that should itself have sub-values, say) that can
be added as a **data change** — populate `parent_id` on the existing rows —
rather than a schema migration.

## 8. The vocabulary panel

`/vocabulary` (sidebar entry above Settings; authenticated, not admin-gated,
matching `/api/facets/*`) is the client for §4's whole cost table plus the
suggestion queue from §3. Before this it had none — the CRUD routes, the
suggestion-queue routes and the colour routes all shipped and deployed with
nothing to call them but a script or `/docs`.

Three tabs, local state (no sub-routes):

- **Facets** — every facet's values: rename, alias, merge, delete, colour,
  and creating a new facet or value.
- **Senders** — a sender's chart split colour only; renaming, merging or
  deleting a sender is an admin taxonomy operation with its own panel
  ([admin.md](admin.md)), not this one.
- **Suggestions** — the pending queue from §3: accept (shows the key it will
  derive before creating it) or dismiss.

**A merge previews before it applies, and the preview is target-specific.**
Reached from a value's Merge action, `/vocabulary/:facetKey/:valueKey/merge`
is a full confirmation page rather than a modal (the same GOV.UK
confirmation-page-for-a-destructive-action convention `router/index.ts`
already uses for document delete). It runs `POST .../merge` with `dry_run:
true` for whichever target is currently selected and shows the diff only once
that dry run has returned **for that same target** — changing the target
selector invalidates the previous count immediately, before the new dry run
resolves, so the page can never show a count for target A beside an Apply
button that would merge into target B. Of the diff's four parts, only the
moved-document count comes from that response; the other three — the target
gaining the source's key as an alias, gaining the source's other aliases
(skipping any it already has), and the source row being deleted along with
its own colour override — are computed from the vocabulary already loaded in
the browser, since a merge's own SQL doesn't need to report anything the
client can't already derive.

**A blocked delete renders the server's reason verbatim.** `DELETE
.../values/{value}`'s `409` `detail` — `"{facet}={value} is on N documents"`
(§1.23.6's `label-counts` number) — is shown as-is, not replaced with a
generic message; that string is the only thing that tells the owner how many
documents to relabel before the value can go. Adding an alias the value
already has is checked client-side first (case-insensitively, matching the
labeller's own casefolded resolution — §3) and reported as `Already covered
by the alias '<x>' — aliases match case-insensitively.` rather than let
through to the server's idempotent `200`, which would otherwise look like a
successful addition of something new.

**Colour is a validated six-slot palette, not a free field, and null is the
normal state** (§4.1). The picker offers the six `SPLIT_PALETTE` swatches
plus a Default choice that clears the override back to the slot the value
would derive anyway; there is no hex input, because the column's `CHECK`
constraint already makes storage safe and a free field's only remaining risk
is legibility — a colour invisible in dark mode or indistinguishable from its
neighbour, which a constrained choice prevents and a validated storage format
cannot. Two values deriving the same colour within one facet is expected —
six slots against `category`'s nineteen values guarantees it — and the panel
marks that collision rather than hiding it, since a picker alone can show
what colour a value has but never that two of them are the same. The
picker component ships wired into this panel only; mounting it on a chart
legend's swatch is later work (charts-view design §4.7).

**Creating a facet or a value is free and carries no documents until a
labelling pass runs.** `library label-archive` is CLI-only (§5) — no route
exposes it — so the panel's create-facet success state says so plainly
rather than implying the new facet is immediately in use, following the
`docs/charts.md` §13 rule that nothing is excluded, or in this case
populated, silently.

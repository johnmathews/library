# Quote kind + inline create-kind (W6)

Adds a `quote` document kind and the ability to create new kinds inline from
the document-detail Kind dropdown, instead of being limited to the seeded set.

## What changed

- **Migration `0017_seed_quote_kind`** — seeds a `quote` kind row (idempotent:
  skips the insert if a `quote` already exists; downgrade removes it only when
  no document references it, since the FK is `ON DELETE SET NULL`).
- **Backend service `taxonomy.create_kind`** — slugifies the name
  (`slugify_kind`, mirroring `projects.slugify` but falling back to `"kind"`),
  sentence-cases the display name (`standardize_kind_name`, matching the seeded
  convention "Utility bill"), dedupes case/whitespace-insensitively against
  existing slug **and** name (returns the existing kind), and rejects
  near-duplicates via a small Levenshtein guard (`_is_near_duplicate`).
- **`POST /api/kinds`** in `api/taxonomy.py` — `201` on create, `200` when an
  exact match already exists, flat `409` (`detail`/`existing_slug`/
  `existing_name`) on a near-duplicate, `422` on a blank name. Gated by the
  `/api` authed-user dependency only (like sender/recipient/tag creation via
  document edits), not admin.
- **Frontend** — `createKind()` in `api/taxonomy.ts`; an "Add kind…" sentinel
  in the Kind dropdown that reveals an inline input + confirm (mirroring the
  recipient inline-add), POSTs the kind, selects the returned slug, autosaves,
  and refreshes the shared taxonomy cache. A `409` keeps the input open with the
  conflict surfaced.

## Decisions / notes

- **Near-duplicate threshold** is length-aware: edit distance ≤ 1 when the
  shorter name is ≤ 4 chars, else ≤ 2. This blocks plurals/typos ("Quotes" vs
  "Quote") without falsely rejecting short distinct kinds.
- **Casing standardisation is sentence case**, not title case, to match the
  existing seed names (e.g. "Parking ticket", not "Parking Ticket").
- The worktree already carried unrelated W8 changes to `SeriesChartTile.vue`
  (a separate unit); its pre-existing `vue-tsc` error is not from this work.

## Verification

- `uv run ruff check . && uv run ruff format --check .` — clean.
- `uv run pytest tests/test_taxonomy_api.py tests/test_documents_api.py -q` — 50 passed.
- Frontend: `vitest run` on `DocumentDetailView.spec.ts` + `src/api/__tests__`
  (119 passed) and `eslint` on the changed files — clean.

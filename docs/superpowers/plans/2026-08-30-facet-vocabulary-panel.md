# Facet vocabulary panel implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the missing client for the facet-vocabulary CRUD, suggestion-queue and split-colour routes — all shipped, deployed, and unreachable from the application — as a `/vocabulary` view.

**Architecture:** One new authenticated route `/vocabulary` with three tabs (Facets, Senders, Suggestions) plus a merge confirmation page at its own URL. Typed functions appended to the existing `frontend/src/api/facets.ts` over `apiFetch`. One backend addition: `GET /api/facets/label-counts`, because `GET /api/facets/counts` counts money rows and every write path here counts labels.

**Tech Stack:** Vue 3 `<script setup>` + TypeScript, Vue Router, Tailwind (utility-patterns + `@container`), Vitest, Playwright; FastAPI + SQLAlchemy async + pytest on the backend.

**Spec:** `docs/superpowers/specs/2026-08-30-facet-vocabulary-panel-design.md`

## Global Constraints

- **The repository is PUBLIC.** No real facet value, sender name, address, registration, person or monetary amount in any fixture, test, doc, commit message or PR body. Invent everything. GitGuardian does not catch this class.
- **E2E runs on three viewport projects**: chromium 1280, mobile-webkit 375, tablet-webkit 656. Every assertion must hold on all three.
- **`md:`/`lg:` are viewport queries but content sits in a viewport-minus-sidebar column.** Use `@container` for anything inside the page column. Prove each guard goes red before trusting it.
- **Assert DOM outcomes, never class names.** Tailwind's utilities layer beats `utility-patterns.css` regardless of specificity.
- **`limit <= 100`** on every list call, asserted in a unit test.
- **Mutation-check every test**: break the implementation, watch the test go red, restore it. A test never seen to fail has not been shown to test anything.
- **Mosaic UI**: native inputs, shared `.form-*`/`.btn` classes, uppercase-xs labels, `items-end gap-3` rows, violet accent, `data-testid` on everything a test touches.
- **`make lint` does NOT run eslint or vue-tsc.** Before pushing run all four: `npm run test:unit`, `npm run lint`, `npm run type-check`, and (for the e2e task) `npm run test:e2e`.
- **A `cd frontend &&` in one Bash call moves every later call.** Use absolute paths or re-`cd` to the repo root.
- Commands below assume the repo root `/Users/john/projects/syncthing/agent-lxc/library-4c` unless a `cd` is shown.

## Interfaces defined by this plan

Every task's code refers to these exact names. They are defined in the task named in the last column.

| Name | Signature | Defined in |
| --- | --- | --- |
| `label_counts` | `async def label_counts(session) -> list[tuple[str, str, int]]` | Task 1 |
| `LabelCountsOut` | `{"counts": [{"facet_key": str, "value_key": str, "labelled": int}]}` | Task 1 |
| `PaletteSlot` | `{ name: string; light: string; dark: string }` | Task 2 |
| `SPLIT_PALETTE` | `readonly PaletteSlot[]` (6 slots) | Task 2 |
| `deriveSlot` | `(key: string) => PaletteSlot` | Task 2 |
| `resolveSplitColour` | `(stored: string \| null, key: string, dark: boolean) => string` | Task 2 |
| `FacetValueRef` | gains `colour: string \| null` | Task 3 |
| `FacetValueCount` | `{ facet_key, value_key, documents, first_date, last_date }` | Task 3 |
| `LabelCount` | `{ facet_key: string; value_key: string; labelled: number }` | Task 3 |
| `fetchFacetCounts` | `() => Promise<FacetValueCount[]>` | Task 3 |
| `fetchLabelCounts` | `() => Promise<LabelCount[]>` | Task 3 |
| `createFacet` | `(key, label, ordinal?) => Promise<{ key: string }>` | Task 3 |
| `createValue` | `(facetKey, key, label) => Promise<{ key: string }>` | Task 3 |
| `renameValue` | `(facetKey, valueKey, label) => Promise<FacetValueRef>` | Task 3 |
| `setValueColour` | `(facetKey, valueKey, colour: string \| null) => Promise<FacetValueRef>` | Task 3 |
| `addAlias` | `(facetKey, valueKey, alias) => Promise<{ alias: string }>` | Task 3 |
| `mergeValue` | `(facetKey, valueKey, into, dryRun) => Promise<{ moved: number }>` | Task 3 |
| `deleteValue` | `(facetKey, valueKey) => Promise<void>` | Task 3 |
| `listSuggestions` | `() => Promise<FacetSuggestion[]>` | Task 3 |
| `acceptSuggestion` | `(id) => Promise<{ facet: string; value: string }>` | Task 3 |
| `dismissSuggestion` | `(id) => Promise<{ state: string }>` | Task 3 |
| `setSenderColour` | `(id, colour: string \| null) => Promise<SenderOption>` | Task 3 |
| `SplitColourPicker` | props `{ modelValue: string \| null; slotKey: string; testid: string }`, emits `update:modelValue` | Task 4 |

---

### Task 1: `GET /api/facets/label-counts`, and deleting the second copy

**Files:**
- Modify: `src/library/facets/vocabulary.py` (add `label_counts`; rewrite `delete_value`'s in-use check to call `count_labels`)
- Modify: `src/library/api/facets.py` (new route + response models, beside `facet_counts`)
- Test: `tests/test_api_facets.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `GET /api/facets/label-counts` returning `{"counts": [{"facet_key", "value_key", "labelled"}]}`; `vocabulary.label_counts(session)`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_api_facets.py`. Read the top of that file first for the fixtures in use (`api_client`, `api_database_url`) and copy the seeding helpers' call style from `tests/test_api_spending.py`'s facet-counts block (`_seed_vocabulary`, `_seed_document`, `_run`) — if those helpers are not importable from `test_api_facets.py`, seed through the API instead as the rest of `test_api_facets.py` does, and label documents with `PUT /api/documents/{id}/labels`.

Each test is a case where `label-counts` must **disagree** with `/api/facets/counts`; a fixture where they agree proves nothing.

```python
def test_label_counts_include_a_value_with_no_money_behind_it(
    api_client: TestClient, api_database_url: str
) -> None:
    """The whole reason this route exists. `/api/facets/counts` reads
    `spend_facts`, whose `eligible` CTE requires `amount_total IS NOT NULL`, so
    a value carried only by amountless documents has no row there at all — it
    renders as unused and then 409s on delete."""
    facet = f"lc-{uuid.uuid4().hex[:8]}"
    _seed_vocabulary(api_database_url, facet=facet, values=("amountless", "monied"))
    _seed_document(api_database_url, amount=None, labels={facet: "amountless"})
    _seed_document(
        api_database_url, amount="10.00", kind=AmountKind.PAYMENT_MADE,
        labels={facet: "monied"},
    )

    labelled = api_client.get("/api/facets/label-counts").json()["counts"]
    money = api_client.get("/api/facets/counts").json()["counts"]

    mine = {c["value_key"]: c["labelled"] for c in labelled if c["facet_key"] == facet}
    assert mine == {"amountless": 1, "monied": 1}
    assert {c["value_key"] for c in money if c["facet_key"] == facet} == {"monied"}, (
        "the money route must be unchanged — if this fails, plan 4b's empty "
        "state has been altered underneath it"
    )


def test_label_counts_count_a_soft_deleted_document(
    api_client: TestClient, api_database_url: str
) -> None:
    """`document_labels` rows survive a soft delete and still block a delete,
    so the number shown must include them or it is not the number enforced."""
    facet = f"lc-{uuid.uuid4().hex[:8]}"
    _seed_vocabulary(api_database_url, facet=facet, values=("gone",))
    doc_id = _seed_document(
        api_database_url, amount="10.00", kind=AmountKind.PAYMENT_MADE,
        labels={facet: "gone"},
    )

    async def soft_delete(session: AsyncSession) -> None:
        await session.execute(
            text("UPDATE documents SET deleted_at = now() WHERE id = :id"), {"id": doc_id}
        )

    _run(api_database_url, soft_delete)

    counts = api_client.get("/api/facets/label-counts").json()["counts"]

    gone = next(c for c in counts if c["facet_key"] == facet)
    assert gone["labelled"] == 1
    money = api_client.get("/api/facets/counts").json()["counts"]
    assert not [c for c in money if c["facet_key"] == facet], "excluded from the money route"


def test_a_value_no_document_carries_is_absent_from_label_counts(
    api_client: TestClient, api_database_url: str
) -> None:
    """An unused value has no row, which is what makes it deletable. Paired with
    a carried value under the same facet so the assertion cannot pass by the
    facet being empty."""
    facet = f"lc-{uuid.uuid4().hex[:8]}"
    _seed_vocabulary(api_database_url, facet=facet, values=("unused", "carried"))
    _seed_document(api_database_url, amount=None, labels={facet: "carried"})

    counts = api_client.get("/api/facets/label-counts").json()["counts"]

    assert {c["value_key"] for c in counts if c["facet_key"] == facet} == {"carried"}


def test_the_displayed_count_is_the_count_delete_enforces(
    api_client: TestClient, api_database_url: str
) -> None:
    """The route's entire claim, tied to the operation in one test: the number
    the panel shows and the number the 409 names must be the same number."""
    facet = f"lc-{uuid.uuid4().hex[:8]}"
    _seed_vocabulary(api_database_url, facet=facet, values=("busy",))
    for _ in range(3):
        _seed_document(api_database_url, amount=None, labels={facet: "busy"})

    counts = api_client.get("/api/facets/label-counts").json()["counts"]
    shown = next(c for c in counts if c["facet_key"] == facet)["labelled"]

    response = api_client.delete(f"/api/facets/{facet}/values/busy")

    assert response.status_code == 409
    assert f"is on {shown} documents" in response.json()["detail"]
    assert shown == 3
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd /Users/john/projects/syncthing/agent-lxc/library-4c && uv run pytest tests/test_api_facets.py -q -k label_count
```
Expected: FAIL — 404 on `/api/facets/label-counts` (the route does not exist).

- [ ] **Step 3: Add `label_counts` to `vocabulary.py`**

Place it directly beneath `count_labels`, so the two live together.

```python
async def label_counts(session: AsyncSession) -> list[tuple[str, str, int]]:
    """`(facet_key, value_key, documents)` for every value a document carries.

    The grouped form of `count_labels`, over exactly the same rows and with
    exactly the same absence of filtering — a soft-deleted or amountless
    document still carries its label and still blocks a delete. Values no
    document carries are absent, which is what makes them deletable.
    """
    rows = await session.execute(
        select(Facet.key, FacetValue.key, func.count())
        .select_from(DocumentLabel)
        .join(Facet, Facet.id == DocumentLabel.facet_id)
        .join(FacetValue, FacetValue.id == DocumentLabel.facet_value_id)
        .group_by(Facet.key, FacetValue.key)
        .order_by(func.count().desc(), Facet.key, FacetValue.key)
    )
    return [(facet_key, value_key, int(count)) for facet_key, value_key, count in rows]
```

Check the imports at the top of `vocabulary.py` — `Facet`, `FacetValue`, `DocumentLabel`, `select` and `func` are all already imported by other functions in the file; add nothing that is already there.

- [ ] **Step 4: Delete the second copy in `delete_value`**

`delete_value` currently inlines its own count that duplicates `count_labels` exactly. Replace the inline `select(func.count())...scalar_one()` block with a call:

```python
async def delete_value(session: AsyncSession, facet_key: str, key: str) -> None:
    """Remove an unused value. Refuses while any document still carries it."""
    facet_id, value_id = await _resolve(session, facet_key, key)
    in_use = await count_labels(session, facet_key, key)
    if in_use:
        raise ValueInUseError(f"{facet_key}={key} is on {in_use} documents")
    await session.execute(delete(FacetValueAlias).where(FacetValueAlias.facet_value_id == value_id))
    await session.execute(delete(FacetValue).where(FacetValue.id == value_id))
```

`facet_id` becomes unused — remove it from the tuple unpack (`_facet_id, value_id = ...`) or ruff will flag it. Run `uv run ruff check src/library/facets/vocabulary.py` to confirm which.

This is a deletion, not a comparison test. Do **not** add a test asserting the two counts agree: such a test passes whenever neither copy exercises the branch where they differ.

- [ ] **Step 5: Add the route**

In `src/library/api/facets.py`, directly after `facet_counts`:

```python
class LabelCount(BaseModel):
    facet_key: str
    value_key: str
    labelled: int


class LabelCountsOut(BaseModel):
    counts: list[LabelCount]


@router.get("/facets/label-counts", summary="Documents carrying each facet value")
async def facet_label_counts(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> LabelCountsOut:
    """How many documents carry each value — the number `delete` enforces.

    Deliberately *not* a field on `/facets/counts`, which aggregates
    `spend_facts` and answers a different question: what the empty state can
    propose a chart from. That route excludes amountless, soft-deleted and
    non-canonical documents on purpose, and a value this route reports is
    routinely absent there. Two questions, two routes.
    """
    return LabelCountsOut(
        counts=[
            LabelCount(facet_key=facet_key, value_key=value_key, labelled=count)
            for facet_key, value_key, count in await vocabulary.label_counts(session)
        ]
    )
```

Route-ordering check: FastAPI matches in declaration order and `/facets/{facet_key}/values` is declared later, so a literal `/facets/label-counts` cannot be swallowed. Confirm by running the tests, not by reading.

- [ ] **Step 6: Run the tests to verify they pass**

```bash
cd /Users/john/projects/syncthing/agent-lxc/library-4c && uv run pytest tests/test_api_facets.py -q -k label_count && uv run pytest tests/test_api_spending.py -q -k counts && uv run pytest tests/test_facet_crud.py tests/test_facet_vocabulary.py -q
```
Expected: all PASS. The `test_api_spending.py -k counts` run is the guard that `/api/facets/counts` is untouched — if any of those four go red, a contract plan 4b depends on has been altered and the change is wrong.

- [ ] **Step 7: Mutation-check**

Run each mutation, confirm the named test goes red, then restore:
1. In `label_counts`, add `.where(Document.deleted_at.is_(None))` (importing `Document`) → `test_label_counts_count_a_soft_deleted_document` must fail.
2. In `delete_value`, change `if in_use:` to `if False:` → `test_the_displayed_count_is_the_count_delete_enforces` must fail.
3. Change `label_counts`'s `func.count()` to `func.count().distinct()`… skip this one; instead change the `group_by` to `Facet.key` alone → the first test must fail.

Record the observed failure text for each in the task report.

- [ ] **Step 8: Lint and commit**

```bash
cd /Users/john/projects/syncthing/agent-lxc/library-4c && uv run ruff format src/library/facets/vocabulary.py src/library/api/facets.py tests/test_api_facets.py && uv run ruff check src/library/ tests/ && uv run mypy src/library/facets/vocabulary.py src/library/api/facets.py
git add -A && git commit -m "feat(facets): a label-count route, and delete_value's second copy removed"
```

---

### Task 2: The validated split palette

**Files:**
- Create: `frontend/src/utils/splitPalette.ts`
- Test: `frontend/src/utils/__tests__/splitPalette.spec.ts`

**Interfaces:**
- Consumes: nothing.
- Produces: `PaletteSlot`, `SPLIT_PALETTE`, `deriveSlot(key)`, `resolveSplitColour(stored, key, dark)`.

The six slots below were produced by the `dataviz` skill's `validate_palette.js` on the **all-pairs** pairlist in both modes and both report ALL CHECKS PASS. **Do not adjust a hex.** Changing one silently invalidates the validation; if a change is genuinely needed, re-run the validator and record the new numbers.

- [ ] **Step 1: Write the failing test**

```ts
import { describe, expect, it } from 'vitest'
import { SPLIT_PALETTE, deriveSlot, resolveSplitColour } from '../splitPalette'

describe('splitPalette', () => {
  it('offers six slots, each with a light and a dark step', () => {
    expect(SPLIT_PALETTE).toHaveLength(6)
    for (const slot of SPLIT_PALETTE) {
      expect(slot.light).toMatch(/^#[0-9a-f]{6}$/)
      expect(slot.dark).toMatch(/^#[0-9a-f]{6}$/)
      expect(slot.name).toBeTruthy()
    }
  })

  it('derives the same slot for the same key every time', () => {
    expect(deriveSlot('vehicle-service')).toBe(deriveSlot('vehicle-service'))
  })

  it('derives a slot that is one of the palette slots', () => {
    for (const key of ['a', 'bb', 'ccc', 'alpha-beta', 'x9', '', 'ünïcode']) {
      expect(SPLIT_PALETTE).toContain(deriveSlot(key))
    }
  })

  it('spreads keys across every slot rather than favouring one', () => {
    // A hash that returned a constant, or ignored all but the first character,
    // would pass every test above. This one fails on both.
    const keys = Array.from({ length: 240 }, (_, i) => `value-${i}`)
    const used = new Set(keys.map((k) => deriveSlot(k).name))
    expect(used.size).toBe(SPLIT_PALETTE.length)
  })

  it('resolves a null colour to the derived slot for the mode', () => {
    const slot = deriveSlot('parking')
    expect(resolveSplitColour(null, 'parking', false)).toBe(slot.light)
    expect(resolveSplitColour(null, 'parking', true)).toBe(slot.dark)
  })

  it('resolves a stored palette colour to that slot, dark step included', () => {
    // A stored override is one hex, but the owner picked a *slot*; rendering
    // its light step on a dark chart would be the bug this branch prevents.
    const slot = SPLIT_PALETTE[3]
    expect(resolveSplitColour(slot.light, 'anything', false)).toBe(slot.light)
    expect(resolveSplitColour(slot.light, 'anything', true)).toBe(slot.dark)
  })

  it('returns a colour from outside the palette verbatim in both modes', () => {
    expect(resolveSplitColour('#123456', 'anything', false)).toBe('#123456')
    expect(resolveSplitColour('#123456', 'anything', true)).toBe('#123456')
  })

  it('matches a stored palette colour case-insensitively', () => {
    const slot = SPLIT_PALETTE[0]
    expect(resolveSplitColour(slot.light.toUpperCase(), 'anything', true)).toBe(slot.dark)
  })
})
```

- [ ] **Step 2: Run it and watch it fail**

```bash
cd /Users/john/projects/syncthing/agent-lxc/library-4c/frontend && npm run test:unit -- splitPalette
```
Expected: FAIL — cannot resolve `../splitPalette`.

- [ ] **Step 3: Implement**

```ts
/**
 * The categorical palette for chart split values (facet values and senders).
 *
 * A value's colour is a nullable override over a slot derived from its key
 * (charts-view design §2.5): null is the normal state, so every legend is
 * stably coloured before anyone has chosen anything, and the migration invents
 * no data.
 *
 * The six slots were validated with the `dataviz` skill's validate_palette.js
 * on the ALL-PAIRS pairlist — the correct list here, because a slot is derived
 * by hashing the key, so any two hues can end up side by side in a legend and
 * there is no ordering to check adjacency against. Both modes report ALL CHECKS
 * PASS: light worst CVD ΔE 9.9 (protan) and normal-vision ΔE 19.8; dark worst
 * CVD ΔE 9.3 (deutan) and normal-vision ΔE 17.2.
 *
 * Two light slots and one dark slot fall below 3:1 against the chart surface,
 * so the relief rule applies: **a swatch is never shown alone**. In this panel
 * and in a chart legend it always carries the value's text label beside it.
 * Re-run the validator and update these numbers before changing any hex.
 *
 * Six rather than eight: the eight-hue reference set clears the adjacent
 * pairlist but fails all-pairs (worst normal-vision ΔE 7.1), and no ordering
 * fixes that, because with all pairs in play the pairlist does not depend on
 * order.
 */

export interface PaletteSlot {
  /** Display name in the picker. */
  name: string
  /** The slot's stored identity: this is the hex written to the database. */
  light: string
  /** The same hue re-stepped for the dark chart surface — selected, not flipped. */
  dark: string
}

export const SPLIT_PALETTE: readonly PaletteSlot[] = [
  { name: 'Blue', light: '#1283dc', dark: '#5791ca' },
  { name: 'Orange', light: '#ff6f42', dark: '#b93b09' },
  { name: 'Green', light: '#51ae7f', dark: '#19825f' },
  { name: 'Indigo', light: '#4423da', dark: '#584fcc' },
  { name: 'Plum', light: '#993375', dark: '#ed3297' },
  { name: 'Olive', light: '#876708', dark: '#b08923' },
]

/**
 * The palette slot a value falls in when it has no stored colour.
 *
 * FNV-1a over the key's code units: stable across renders, sessions, machines
 * and releases, and independent of document counts and of how many values the
 * facet has — so a value's colour never moves because the archive changed.
 * Keyed on the value's `key`, never its ordinal or its rank in a chart.
 */
export function deriveSlot(key: string): PaletteSlot {
  let hash = 0x811c9dc5
  for (let i = 0; i < key.length; i += 1) {
    hash ^= key.charCodeAt(i)
    hash = Math.imul(hash, 0x01000193) >>> 0
  }
  return SPLIT_PALETTE[hash % SPLIT_PALETTE.length]
}

/**
 * The colour to paint a split value, for the current theme.
 *
 * Three cases, in order: no stored colour derives a slot from the key; a stored
 * colour that *is* a palette slot's light step resolves to that slot, so an
 * override picked from the palette is theme-aware even though the database
 * holds one hex; anything else is an arbitrary colour from outside the palette
 * (a script, a data migration) with no theme pair to look up, returned as-is.
 */
export function resolveSplitColour(stored: string | null, key: string, dark: boolean): string {
  if (!stored) {
    const slot = deriveSlot(key)
    return dark ? slot.dark : slot.light
  }
  const lower = stored.toLowerCase()
  const slot = SPLIT_PALETTE.find((candidate) => candidate.light === lower)
  if (!slot) return stored
  return dark ? slot.dark : slot.light
}
```

- [ ] **Step 4: Run it and watch it pass**

```bash
cd /Users/john/projects/syncthing/agent-lxc/library-4c/frontend && npm run test:unit -- splitPalette
```
Expected: PASS, 8 tests.

- [ ] **Step 5: Mutation-check**

1. `return SPLIT_PALETTE[0]` from `deriveSlot` → the spread test must fail.
2. Drop the `dark ? slot.dark :` branch in the stored-colour case (return `stored`) → the stored-palette-colour test must fail.
3. Remove `.toLowerCase()` → the case-insensitive test must fail.

Restore after each. Record the failure text.

- [ ] **Step 6: Commit**

```bash
cd /Users/john/projects/syncthing/agent-lxc/library-4c && git add -A && git commit -m "feat(charts): the validated split-value palette"
```

---

### Task 3: The API client

**Files:**
- Modify: `frontend/src/api/facets.ts` (append; do **not** rewrite `fetchFacets`)
- Modify: `frontend/src/api/taxonomy.ts` (`SenderOption` gains `colour`; add `setSenderColour`)
- Test: `frontend/src/api/__tests__/facets.spec.ts` (create or extend — check whether it exists first)

**Interfaces:**
- Consumes: nothing.
- Produces: every client function in the interface table above.

**If plan 4b has already landed `fetchFacetCounts` in `facets.ts`, use theirs and do not write a second one.** Check with `grep -n fetchFacetCounts frontend/src/api/facets.ts` before starting.

- [ ] **Step 1: Write the failing tests**

The point of these tests is the **request**, not the response. Stub `fetch` and assert on what was sent.

```ts
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  addAlias, createFacet, createValue, deleteValue, fetchLabelCounts, mergeValue,
  renameValue, setValueColour,
} from '../facets'
import { setSenderColour } from '../taxonomy'

function stubFetch(body: unknown = {}, status = 200): ReturnType<typeof vi.fn> {
  const spy = vi.fn().mockResolvedValue({
    ok: status < 400,
    status,
    json: async () => body,
  } as Response)
  vi.stubGlobal('fetch', spy)
  return spy
}

const bodyOf = (spy: ReturnType<typeof vi.fn>): Record<string, unknown> =>
  JSON.parse(spy.mock.calls[0][1].body as string)

afterEach(() => vi.unstubAllGlobals())

describe('the value-edit calls send exactly one field each', () => {
  // The API tells "clear it" from "do not touch it" by whether the key is
  // present (model_fields_set), so a client that spread a form object — which
  // always carries every key — would clear a colour on every rename. Two narrow
  // functions make that unrepresentable; this test is what keeps them narrow.
  it('renameValue sends only label', async () => {
    const spy = stubFetch({ key: 'a', label: 'A', parent_id: null, aliases: [], colour: null })
    await renameValue('category', 'alpha', 'Alpha')
    expect(Object.keys(bodyOf(spy))).toEqual(['label'])
  })

  it('setValueColour sends only colour', async () => {
    const spy = stubFetch({ key: 'a', label: 'A', parent_id: null, aliases: [], colour: '#1283dc' })
    await setValueColour('category', 'alpha', '#1283dc')
    expect(Object.keys(bodyOf(spy))).toEqual(['colour'])
    expect(bodyOf(spy).colour).toBe('#1283dc')
  })

  it('setValueColour(null) sends an explicit null, which survives serialisation', async () => {
    const spy = stubFetch({ key: 'a', label: 'A', parent_id: null, aliases: [], colour: null })
    await setValueColour('category', 'alpha', null)
    expect(spy.mock.calls[0][1].body).toBe('{"colour":null}')
  })

  it('setSenderColour(null) sends an explicit null too', async () => {
    const spy = stubFetch({ id: 1, name: 'X', document_count: 0, colour: null })
    await setSenderColour(1, null)
    expect(spy.mock.calls[0][1].body).toBe('{"colour":null}')
  })
})

describe('routes and methods', () => {
  it('createFacet posts to /api/facets', async () => {
    const spy = stubFetch({ key: 'k' })
    await createFacet('k', 'K', 3)
    expect(spy.mock.calls[0][0]).toBe('/api/facets')
    expect(spy.mock.calls[0][1].method).toBe('POST')
    expect(bodyOf(spy)).toEqual({ key: 'k', label: 'K', ordinal: 3 })
  })

  it('createValue posts under the facet', async () => {
    const spy = stubFetch({ key: 'v' })
    await createValue('category', 'v', 'V')
    expect(spy.mock.calls[0][0]).toBe('/api/facets/category/values')
  })

  it('addAlias posts to the aliases sub-resource', async () => {
    const spy = stubFetch({ alias: 'x' })
    await addAlias('category', 'alpha', 'x')
    expect(spy.mock.calls[0][0]).toBe('/api/facets/category/values/alpha/aliases')
  })

  it('mergeValue carries the target and the dry_run flag', async () => {
    const spy = stubFetch({ moved: 4 })
    const result = await mergeValue('category', 'alpha', 'beta', true)
    expect(spy.mock.calls[0][0]).toBe('/api/facets/category/values/alpha/merge')
    expect(bodyOf(spy)).toEqual({ into: 'beta', dry_run: true })
    expect(result.moved).toBe(4)
  })

  it('mergeValue defaults to a real merge only when told to', async () => {
    const spy = stubFetch({ moved: 4 })
    await mergeValue('category', 'alpha', 'beta', false)
    expect(bodyOf(spy).dry_run).toBe(false)
  })

  it('deleteValue issues a DELETE and tolerates the 204 empty body', async () => {
    const spy = vi.fn().mockResolvedValue({ ok: true, status: 204 } as Response)
    vi.stubGlobal('fetch', spy)
    await expect(deleteValue('category', 'alpha')).resolves.toBeUndefined()
    expect(spy.mock.calls[0][1].method).toBe('DELETE')
  })

  it('fetchLabelCounts unwraps the counts array', async () => {
    stubFetch({ counts: [{ facet_key: 'category', value_key: 'alpha', labelled: 7 }] })
    await expect(fetchLabelCounts()).resolves.toEqual([
      { facet_key: 'category', value_key: 'alpha', labelled: 7 },
    ])
  })

  it('no list call this module makes asks for more than 100 rows', async () => {
    // GET /api/documents 422s above limit 100; asserted here because a mocked
    // fetch will never enforce it.
    const spy = stubFetch({ counts: [] })
    await fetchLabelCounts()
    const url = String(spy.mock.calls[0][0])
    const limit = new URL(url, 'http://x').searchParams.get('limit')
    expect(limit === null || Number(limit) <= 100).toBe(true)
  })
})
```

- [ ] **Step 2: Run and watch it fail**

```bash
cd /Users/john/projects/syncthing/agent-lxc/library-4c/frontend && npm run test:unit -- api/__tests__/facets
```
Expected: FAIL — the named exports do not exist.

- [ ] **Step 3: Implement in `facets.ts`**

Append; leave `fetchFacets`, `fetchDocumentLabels`, `updateDocumentLabels` and `facetQueryParams` exactly as they are. Add `colour` to the existing `FacetValueRef` interface.

```ts
export interface FacetValueRef {
  key: string
  label: string
  parent_id: number | null
  aliases: string[]
  /** A stored override; null means the client derives a palette slot from `key`. */
  colour: string | null
}

/** A row of GET /api/facets/counts — money-scoped (see `LabelCount`). */
export interface FacetValueCount {
  facet_key: string
  value_key: string
  /** Documents with an amount, canonical, not deleted. NOT what delete enforces. */
  documents: number
  first_date: string | null
  last_date: string | null
}

/** A row of GET /api/facets/label-counts — what merge moves and delete blocks on. */
export interface LabelCount {
  facet_key: string
  value_key: string
  labelled: number
}

export interface FacetSuggestion {
  id: number
  facet: string
  suggested_label: string
  reason: string | null
  document_id: number
}

/** GET /api/facets/counts — documents *with money* per value, for chart proposals. */
export async function fetchFacetCounts(): Promise<FacetValueCount[]> {
  const body = await apiFetch<{ counts: FacetValueCount[] }>('/api/facets/counts')
  return body.counts
}

/** GET /api/facets/label-counts — documents *carrying* each value. */
export async function fetchLabelCounts(): Promise<LabelCount[]> {
  const body = await apiFetch<{ counts: LabelCount[] }>('/api/facets/label-counts')
  return body.counts
}

/** POST /api/facets — 409 when the key exists. */
export function createFacet(key: string, label: string, ordinal = 0): Promise<{ key: string }> {
  return apiFetch<{ key: string }>('/api/facets', { method: 'POST', body: { key, label, ordinal } })
}

/** POST /api/facets/{facet}/values — 404 unknown facet, 409 duplicate key. */
export function createValue(
  facetKey: string,
  key: string,
  label: string,
): Promise<{ key: string }> {
  return apiFetch<{ key: string }>(`/api/facets/${encodeURIComponent(facetKey)}/values`, {
    method: 'POST',
    body: { key, label },
  })
}

/**
 * PATCH .../values/{value} with ONLY `label`.
 *
 * Deliberately one field per call. The route tells "clear it" from "leave it
 * alone" by whether the key is present in the body, so a single patch function
 * taking an object would clear a colour every time a caller spread a form.
 * "Leave the colour alone" is expressed by calling this function rather than
 * `setValueColour`.
 */
export function renameValue(
  facetKey: string,
  valueKey: string,
  label: string,
): Promise<FacetValueRef> {
  return apiFetch<FacetValueRef>(
    `/api/facets/${encodeURIComponent(facetKey)}/values/${encodeURIComponent(valueKey)}`,
    { method: 'PATCH', body: { label } },
  )
}

/** PATCH .../values/{value} with ONLY `colour`. `null` clears it (see `renameValue`). */
export function setValueColour(
  facetKey: string,
  valueKey: string,
  colour: string | null,
): Promise<FacetValueRef> {
  return apiFetch<FacetValueRef>(
    `/api/facets/${encodeURIComponent(facetKey)}/values/${encodeURIComponent(valueKey)}`,
    { method: 'PATCH', body: { colour } },
  )
}

/** POST .../aliases — idempotent server-side (ON CONFLICT DO NOTHING). */
export function addAlias(
  facetKey: string,
  valueKey: string,
  alias: string,
): Promise<{ alias: string }> {
  return apiFetch<{ alias: string }>(
    `/api/facets/${encodeURIComponent(facetKey)}/values/${encodeURIComponent(valueKey)}/aliases`,
    { method: 'POST', body: { alias } },
  )
}

/**
 * POST .../merge. With `dryRun` it writes nothing and returns the number of
 * labels the real merge would move — and 404s on an unknown target exactly as
 * the real merge would, so a preview fails on anything the merge would fail on.
 */
export function mergeValue(
  facetKey: string,
  valueKey: string,
  into: string,
  dryRun: boolean,
): Promise<{ moved: number }> {
  return apiFetch<{ moved: number }>(
    `/api/facets/${encodeURIComponent(facetKey)}/values/${encodeURIComponent(valueKey)}/merge`,
    { method: 'POST', body: { into, dry_run: dryRun } },
  )
}

/** DELETE .../values/{value} — 204, or 409 whose `detail` names the document count. */
export function deleteValue(facetKey: string, valueKey: string): Promise<void> {
  return apiFetch<void>(
    `/api/facets/${encodeURIComponent(facetKey)}/values/${encodeURIComponent(valueKey)}`,
    { method: 'DELETE' },
  )
}

/** GET /api/facet-suggestions — up to 100 pending, oldest first (server-capped). */
export async function listSuggestions(): Promise<FacetSuggestion[]> {
  const body = await apiFetch<{ suggestions: FacetSuggestion[] }>('/api/facet-suggestions')
  return body.suggestions
}

/** POST /api/facet-suggestions/{id}/accept — creates the value AND labels the document. */
export function acceptSuggestion(id: number): Promise<{ facet: string; value: string }> {
  return apiFetch<{ facet: string; value: string }>(`/api/facet-suggestions/${id}/accept`, {
    method: 'POST',
  })
}

/** POST /api/facet-suggestions/{id}/dismiss — rejects without creating anything. */
export function dismissSuggestion(id: number): Promise<{ state: string }> {
  return apiFetch<{ state: string }>(`/api/facet-suggestions/${id}/dismiss`, { method: 'POST' })
}
```

In `taxonomy.ts`, add `colour: string | null` to `SenderOption` and:

```ts
/**
 * PATCH /api/senders/{id} — set or clear a sender's chart colour, and nothing
 * else: a sender's name comes from ingested documents and renaming one is an
 * admin taxonomy operation with its own merge semantics. `null` clears the
 * override; not calling this leaves it alone (see `setValueColour`).
 */
export function setSenderColour(id: number, colour: string | null): Promise<SenderOption> {
  return apiFetch<SenderOption>(`/api/senders/${id}`, { method: 'PATCH', body: { colour } })
}
```

- [ ] **Step 4: Run and watch it pass**

```bash
cd /Users/john/projects/syncthing/agent-lxc/library-4c/frontend && npm run test:unit -- api/__tests__/facets && npm run type-check
```

- [ ] **Step 5: Mutation-check**

1. Merge `renameValue` and `setValueColour` into one function taking `{label?, colour?}` and call it with `{label: 'X', colour: undefined}` → the one-key tests must fail.
2. Change `setValueColour`'s body to `colour ?? undefined` → the explicit-null test must fail.

Restore. Record the failure text.

- [ ] **Step 6: Commit**

```bash
cd /Users/john/projects/syncthing/agent-lxc/library-4c && git add -A && git commit -m "feat(facets): a client for the vocabulary CRUD, suggestion and colour routes"
```

---

### Task 4: `SplitColourPicker.vue`

**Files:**
- Create: `frontend/src/components/vocabulary/SplitColourPicker.vue`
- Test: `frontend/src/components/vocabulary/__tests__/SplitColourPicker.spec.ts`

**Interfaces:**
- Consumes: `SPLIT_PALETTE`, `deriveSlot`, `resolveSplitColour` (Task 2).
- Produces: a component with props `{ modelValue: string | null; slotKey: string; testid: string }` emitting `update:modelValue` with `string | null`.

The component ships **standalone and unwired to any chart**. Charts-view spec §4.7 wants it on 4b's legend swatch; `components/charts/` belongs to plans 4b and 5 and must not be touched here.

- [ ] **Step 1: Write the failing test**

```ts
import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import SplitColourPicker from '../SplitColourPicker.vue'
import { SPLIT_PALETTE, deriveSlot } from '@/utils/splitPalette'

const factory = (modelValue: string | null = null) =>
  mount(SplitColourPicker, { props: { modelValue, slotKey: 'alpha', testid: 'v-alpha' } })

describe('SplitColourPicker', () => {
  it('offers every palette slot plus a default choice', () => {
    const wrapper = factory()
    expect(wrapper.findAll('[data-testid^="v-alpha-swatch-"]')).toHaveLength(SPLIT_PALETTE.length)
    expect(wrapper.find('[data-testid="v-alpha-default"]').exists()).toBe(true)
  })

  it('offers no free-text colour input', () => {
    // Restricted to the palette by design: a free field lets the owner pick
    // something invisible in dark mode and nothing could prevent it.
    const wrapper = factory()
    expect(wrapper.find('input[type="color"]').exists()).toBe(false)
    expect(wrapper.find('input[type="text"]').exists()).toBe(false)
  })

  it('emits the slot light hex when a swatch is chosen', async () => {
    const wrapper = factory()
    await wrapper.find('[data-testid="v-alpha-swatch-2"]').trigger('click')
    expect(wrapper.emitted('update:modelValue')?.[0]).toEqual([SPLIT_PALETTE[2].light])
  })

  it('emits null when the default is chosen, which is how a colour is cleared', async () => {
    const wrapper = factory(SPLIT_PALETTE[1].light)
    await wrapper.find('[data-testid="v-alpha-default"]').trigger('click')
    expect(wrapper.emitted('update:modelValue')?.[0]).toEqual([null])
  })

  it('marks the stored colour as selected', () => {
    const wrapper = factory(SPLIT_PALETTE[4].light)
    expect(wrapper.find('[data-testid="v-alpha-swatch-4"]').attributes('aria-pressed')).toBe('true')
    expect(wrapper.find('[data-testid="v-alpha-default"]').attributes('aria-pressed')).toBe('false')
  })

  it('marks the default as selected when nothing is stored', () => {
    const wrapper = factory(null)
    expect(wrapper.find('[data-testid="v-alpha-default"]').attributes('aria-pressed')).toBe('true')
  })

  it('names the slot the key would derive, so the owner sees what default means', () => {
    const wrapper = factory(null)
    expect(wrapper.find('[data-testid="v-alpha-default"]').text()).toContain(
      deriveSlot('alpha').name,
    )
  })

  it('labels every swatch by name, never by colour alone', () => {
    // The relief rule: three slots fall below 3:1 against the chart surface, so
    // a swatch must never be the only carrier of identity.
    const wrapper = factory()
    for (const [index, slot] of SPLIT_PALETTE.entries()) {
      expect(
        wrapper.find(`[data-testid="v-alpha-swatch-${index}"]`).attributes('aria-label'),
      ).toContain(slot.name)
    }
  })
})
```

- [ ] **Step 2: Run and watch it fail**

```bash
cd /Users/john/projects/syncthing/agent-lxc/library-4c/frontend && npm run test:unit -- SplitColourPicker
```

- [ ] **Step 3: Implement**

Render a `<div role="group">` containing one `<button type="button">` per slot plus the default button. Requirements the tests above pin down, restated so they are not lost:
- swatch testids are `${testid}-swatch-${index}`, the default is `${testid}-default`;
- `aria-pressed` reflects selection, computed by comparing `modelValue?.toLowerCase()` with `slot.light`;
- `aria-label` is `${slot.name}` (plus any extra wording you like — the test uses `toContain`);
- the default button's text contains `deriveSlot(props.slotKey).name`;
- clicking a swatch emits its `light` hex; clicking default emits `null`;
- no `<input type="color">` and no text input anywhere.

Styling: mosaic conventions. Swatches are `w-6 h-6 rounded` buttons with a `border border-gray-300 dark:border-gray-600` ring, `ring-2 ring-violet-500 ring-offset-1` when pressed, laid out `flex flex-wrap items-center gap-2`. Set the background with an inline `:style="{ backgroundColor: slot.light }"` — a Tailwind class cannot carry a runtime hex. The default button is a `.btn-xs` style text button, not a swatch.

- [ ] **Step 4: Run and watch it pass**

```bash
cd /Users/john/projects/syncthing/agent-lxc/library-4c/frontend && npm run test:unit -- SplitColourPicker && npm run type-check && npm run lint
```

- [ ] **Step 5: Mutation-check**

1. Emit `slot.dark` instead of `slot.light` → the swatch-emit test must fail.
2. Emit `''` instead of `null` from the default → the clear test must fail.
3. Add an `<input type="color">` → the no-free-field test must fail.

Restore. Record the failure text.

- [ ] **Step 6: Commit**

```bash
cd /Users/john/projects/syncthing/agent-lxc/library-4c && git add -A && git commit -m "feat(vocabulary): a palette-restricted split colour picker"
```

---

### Task 5: The `/vocabulary` route, view shell and navigation

**Files:**
- Create: `frontend/src/views/VocabularyView.vue`
- Create: `frontend/src/views/vocabulary/FacetsPanel.vue` (placeholder body this task; filled in Task 6)
- Create: `frontend/src/views/vocabulary/SendersPanel.vue` (placeholder; Task 8)
- Create: `frontend/src/views/vocabulary/SuggestionsPanel.vue` (placeholder; Task 9)
- Modify: `frontend/src/router/index.ts`
- Modify: `frontend/src/components/layout/AppSidebar.vue`
- Test: `frontend/src/views/__tests__/VocabularyView.spec.ts`

**Interfaces:**
- Consumes: nothing.
- Produces: route name `vocabulary` at `/vocabulary`; the three panels, each accepting a prop `active: boolean` and self-loading on the first `false → true` transition, matching `AdminMetadataPanel`'s lazy pattern.

**Overlap with plan 4b:** both plans edit `router/index.ts` and `AppSidebar.vue`. Both edits are additive. If a rebase conflicts, keep both entries.

- [ ] **Step 1: Write the failing test**

```ts
import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import VocabularyView from '../VocabularyView.vue'
import { routes } from '@/router'

const stubs = {
  PageHeader: true,
  FacetsPanel: true,
  SendersPanel: true,
  SuggestionsPanel: true,
}

describe('VocabularyView', () => {
  it('is routed at /vocabulary', () => {
    expect(routes.some((r) => r.path === '/vocabulary' && r.name === 'vocabulary')).toBe(true)
  })

  it('opens on the Facets tab', () => {
    const wrapper = mount(VocabularyView, { global: { stubs } })
    expect(wrapper.find('[data-testid="vocab-tab-facets-btn"]').attributes('aria-selected'))
      .toBe('true')
  })

  it('shows the facets panel and hides the others until their tab is chosen', async () => {
    // v-show, so assert on the rendered element's visibility, not on classes.
    const wrapper = mount(VocabularyView, { global: { stubs } })
    expect(wrapper.find('[data-testid="vocab-tab-senders"]').isVisible()).toBe(false)

    await wrapper.find('[data-testid="vocab-tab-senders-btn"]').trigger('click')

    expect(wrapper.find('[data-testid="vocab-tab-senders"]').isVisible()).toBe(true)
    expect(wrapper.find('[data-testid="vocab-tab-facets"]').isVisible()).toBe(false)
  })

  it('tells each panel whether it is the open tab, so it can load lazily', async () => {
    const wrapper = mount(VocabularyView, { global: { stubs } })
    const senders = () => wrapper.findComponent({ name: 'SendersPanel' })
    expect(senders().props('active')).toBe(false)

    await wrapper.find('[data-testid="vocab-tab-senders-btn"]').trigger('click')

    expect(senders().props('active')).toBe(true)
  })

  it('offers all three tabs', () => {
    const wrapper = mount(VocabularyView, { global: { stubs } })
    for (const tab of ['facets', 'senders', 'suggestions']) {
      expect(wrapper.find(`[data-testid="vocab-tab-${tab}-btn"]`).exists()).toBe(true)
    }
  })
})
```

- [ ] **Step 2: Run and watch it fail**

```bash
cd /Users/john/projects/syncthing/agent-lxc/library-4c/frontend && npm run test:unit -- VocabularyView
```

- [ ] **Step 3: Implement the view**

Copy `AdminView.vue`'s structure exactly — same `role="tablist"`, `role="tab"`, `aria-selected`, `tabindex`, `tabClass` helper and `v-show` panels — with `Tab = 'facets' | 'senders' | 'suggestions'`, testids `vocab-tab-*-btn` and `vocab-tab-*`, and `<PageHeader title="Vocabulary" />`. Pass `:active="tab === '<id>'"` to each panel.

Each of the three panel files this task creates is a stub whose only job is to have the right name and accept the prop:

```vue
<script setup lang="ts">
defineProps<{ active: boolean }>()
</script>

<template>
  <div class="card p-6">Coming in the next task.</div>
</template>
```

Give each a `defineOptions({ name: 'SendersPanel' })` (and equivalents) so `findComponent({ name: ... })` resolves.

- [ ] **Step 4: Add the route and the sidebar entry**

In `frontend/src/router/index.ts`, beside the `/settings` entry:

```ts
  {
    // The facet vocabulary: the closed set every chart splits by, plus the
    // split colours. Its CRUD routes are authenticated but not admin-gated, so
    // neither is this (docs/facets.md).
    path: '/vocabulary',
    name: 'vocabulary',
    component: () => import('../views/VocabularyView.vue'),
  },
```

In `AppSidebar.vue`, copy the `/settings` `RouterLink v-slot` block verbatim, immediately above it, changing `to` to `/vocabulary`, the label to `Vocabulary`, and the icon path. Read the neighbouring blocks and match their structure exactly — the sidebar has collapsed and expanded states and both must work.

- [ ] **Step 5: Run and watch it pass**

```bash
cd /Users/john/projects/syncthing/agent-lxc/library-4c/frontend && npm run test:unit -- VocabularyView && npm run type-check && npm run lint
```

- [ ] **Step 6: See it in a browser**

```bash
cd /Users/john/projects/syncthing/agent-lxc/library-4c/frontend && npm run dev
```
Open `/vocabulary`, confirm the sidebar entry appears in both the collapsed and expanded sidebar states and that all three tabs switch. Then stop the server.

- [ ] **Step 7: Commit**

```bash
cd /Users/john/projects/syncthing/agent-lxc/library-4c && git add -A && git commit -m "feat(vocabulary): the /vocabulary route, tab shell and nav entry"
```

---

### Task 6: The Facets panel

**Files:**
- Modify: `frontend/src/views/vocabulary/FacetsPanel.vue` (replace the stub)
- Create: `frontend/src/utils/slugify.ts`
- Test: `frontend/src/views/vocabulary/__tests__/FacetsPanel.spec.ts`
- Test: `frontend/src/utils/__tests__/slugify.spec.ts`

**Interfaces:**
- Consumes: every read/write client function from Task 3; `SplitColourPicker` (Task 4); `resolveSplitColour`, `deriveSlot`, `SPLIT_PALETTE` (Task 2).
- Produces: nothing later tasks depend on.

**Layout constraint.** A value row carries a swatch, label, key, aliases, two counts, a date span and four actions inside a column that is the viewport minus the sidebar. Use `@container` on the row's wrapper (`class="@container"` on the facet card, `@md:` / `@lg:` variants on the row), never `md:`/`lg:`. This has gone wrong twice in this repository for exactly this reason.

- [ ] **Step 1: Write the failing tests**

Mock the API module, mount, and assert on rendered text and on what was called. Full file:

```ts
import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import FacetsPanel from '../FacetsPanel.vue'
import { ApiError } from '@/api/client'
import { SPLIT_PALETTE } from '@/utils/splitPalette'

vi.mock('@/api/facets', () => ({
  fetchFacets: vi.fn(),
  fetchFacetCounts: vi.fn(),
  fetchLabelCounts: vi.fn(),
  createFacet: vi.fn(),
  createValue: vi.fn(),
  renameValue: vi.fn(),
  setValueColour: vi.fn(),
  addAlias: vi.fn(),
  deleteValue: vi.fn(),
}))
import * as api from '@/api/facets'

const VOCAB = [
  {
    key: 'category',
    label: 'Category',
    ordinal: 0,
    values: [
      { key: 'alpha', label: 'Alpha', parent_id: null, aliases: ['a-one'], colour: null },
      { key: 'beta', label: 'Beta', parent_id: null, aliases: [], colour: SPLIT_PALETTE[2].light },
    ],
  },
]

beforeEach(() => {
  vi.mocked(api.fetchFacets).mockResolvedValue(structuredClone(VOCAB))
  vi.mocked(api.fetchFacetCounts).mockResolvedValue([
    { facet_key: 'category', value_key: 'alpha', documents: 2, first_date: '2026-01-01', last_date: '2026-02-01' },
  ])
  vi.mocked(api.fetchLabelCounts).mockResolvedValue([
    { facet_key: 'category', value_key: 'alpha', labelled: 7 },
  ])
})

const open = async () => {
  const wrapper = mount(FacetsPanel, {
    props: { active: true },
    global: { stubs: { SplitColourPicker: true } },
  })
  await flushPromises()
  return wrapper
}

describe('FacetsPanel', () => {
  it('does not load until its tab is opened', async () => {
    mount(FacetsPanel, { props: { active: false }, global: { stubs: { SplitColourPicker: true } } })
    await flushPromises()
    expect(api.fetchFacets).not.toHaveBeenCalled()
  })

  it('shows both counts, distinctly labelled', async () => {
    // The money count and the label count answer different questions and
    // routinely differ; showing one number would misrepresent the other.
    const wrapper = await open()
    const row = wrapper.find('[data-testid="value-category-alpha"]')
    expect(row.text()).toContain('7 labelled')
    expect(row.text()).toContain('2 in charts')
  })

  it('shows a value no document carries as zero labelled, not blank', async () => {
    const wrapper = await open()
    expect(wrapper.find('[data-testid="value-category-beta"]').text()).toContain('0 labelled')
  })

  it('renames a value with a label-only request', async () => {
    vi.mocked(api.renameValue).mockResolvedValue({
      key: 'alpha', label: 'Renamed', parent_id: null, aliases: [], colour: null,
    })
    const wrapper = await open()
    await wrapper.find('[data-testid="value-category-alpha-rename-btn"]').trigger('click')
    await wrapper.find('[data-testid="value-category-alpha-rename-input"]').setValue('Renamed')
    await wrapper.find('[data-testid="value-category-alpha-rename-save"]').trigger('click')
    await flushPromises()
    expect(api.renameValue).toHaveBeenCalledWith('category', 'alpha', 'Renamed')
  })

  it('refuses to add an alias the value already has, without calling the API', async () => {
    // The route is idempotent (ON CONFLICT DO NOTHING), so it would answer 200
    // and the panel would report a phantom addition.
    const wrapper = await open()
    await wrapper.find('[data-testid="value-category-alpha-alias-btn"]').trigger('click')
    await wrapper.find('[data-testid="value-category-alpha-alias-input"]').setValue('a-one')
    await wrapper.find('[data-testid="value-category-alpha-alias-save"]').trigger('click')
    await flushPromises()
    expect(api.addAlias).not.toHaveBeenCalled()
    expect(wrapper.find('[data-testid="value-category-alpha-error"]').text())
      .toContain('already an alias')
  })

  it('adds an alias the value does not have', async () => {
    vi.mocked(api.addAlias).mockResolvedValue({ alias: 'a-two' })
    const wrapper = await open()
    await wrapper.find('[data-testid="value-category-alpha-alias-btn"]').trigger('click')
    await wrapper.find('[data-testid="value-category-alpha-alias-input"]').setValue('a-two')
    await wrapper.find('[data-testid="value-category-alpha-alias-save"]').trigger('click')
    await flushPromises()
    expect(api.addAlias).toHaveBeenCalledWith('category', 'alpha', 'a-two')
  })

  it("renders the server's reason when a delete is refused", async () => {
    vi.mocked(api.deleteValue).mockRejectedValue(
      new ApiError(409, 'category=alpha is on 7 documents', {
        detail: 'category=alpha is on 7 documents',
      }),
    )
    const wrapper = await open()
    await wrapper.find('[data-testid="value-category-alpha-delete-btn"]').trigger('click')
    await wrapper.find('[data-testid="value-category-alpha-delete-confirm"]').trigger('click')
    await flushPromises()
    expect(wrapper.find('[data-testid="value-category-alpha-error"]').text())
      .toContain('category=alpha is on 7 documents')
  })

  it('sets a colour through the picker', async () => {
    vi.mocked(api.setValueColour).mockResolvedValue({
      key: 'alpha', label: 'Alpha', parent_id: null, aliases: ['a-one'],
      colour: SPLIT_PALETTE[1].light,
    })
    const wrapper = await open()
    wrapper.findComponent({ name: 'SplitColourPicker' }).vm.$emit(
      'update:modelValue', SPLIT_PALETTE[1].light,
    )
    await flushPromises()
    expect(api.setValueColour).toHaveBeenCalledWith('category', 'alpha', SPLIT_PALETTE[1].light)
  })

  it('clears a colour when the picker emits null', async () => {
    vi.mocked(api.setValueColour).mockResolvedValue({
      key: 'alpha', label: 'Alpha', parent_id: null, aliases: ['a-one'], colour: null,
    })
    const wrapper = await open()
    wrapper.findComponent({ name: 'SplitColourPicker' }).vm.$emit('update:modelValue', null)
    await flushPromises()
    expect(api.setValueColour).toHaveBeenCalledWith('category', 'alpha', null)
  })

  it('marks two values in one facet that resolve to the same colour', async () => {
    // Six slots over nineteen values makes collisions arithmetic, and a picker
    // alone never tells the owner two values look identical.
    vi.mocked(api.fetchFacets).mockResolvedValue([
      {
        key: 'category', label: 'Category', ordinal: 0,
        values: [
          { key: 'one', label: 'One', parent_id: null, aliases: [], colour: SPLIT_PALETTE[0].light },
          { key: 'two', label: 'Two', parent_id: null, aliases: [], colour: SPLIT_PALETTE[0].light },
          { key: 'three', label: 'Three', parent_id: null, aliases: [], colour: SPLIT_PALETTE[1].light },
        ],
      },
    ])
    const wrapper = await open()
    expect(wrapper.find('[data-testid="value-category-one-collision"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="value-category-two-collision"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="value-category-three-collision"]').exists()).toBe(false)
  })

  it('says a new facet carries no documents until a labelling pass runs', async () => {
    // Creating a facet is free and changes nothing; reporting only success
    // would be silently untrue.
    vi.mocked(api.createFacet).mockResolvedValue({ key: 'newfacet' })
    const wrapper = await open()
    await wrapper.find('[data-testid="create-facet-key"]').setValue('newfacet')
    await wrapper.find('[data-testid="create-facet-label"]').setValue('New facet')
    await wrapper.find('[data-testid="create-facet-save"]').trigger('click')
    await flushPromises()
    expect(wrapper.find('[data-testid="create-facet-note"]').text()).toContain('label-archive')
  })

  it('prefills a new value key from its label but leaves it editable', async () => {
    const wrapper = await open()
    await wrapper.find('[data-testid="create-value-category-btn"]').trigger('click')
    await wrapper.find('[data-testid="create-value-category-label"]').setValue('EV charging (home)!')
    await flushPromises()
    const key = wrapper.find('[data-testid="create-value-category-key"]')
    expect((key.element as HTMLInputElement).value).toBe('ev-charging-home')
    await key.setValue('something-else')
    await wrapper.find('[data-testid="create-value-category-save"]').trigger('click')
    await flushPromises()
    expect(api.createValue).toHaveBeenCalledWith('category', 'something-else', 'EV charging (home)!')
  })

  it('renders a 422 on an unusable key as the server states it', async () => {
    vi.mocked(api.createValue).mockRejectedValue(
      new ApiError(422, 'nothing matching [a-z0-9_-] remains', {}),
    )
    const wrapper = await open()
    await wrapper.find('[data-testid="create-value-category-btn"]').trigger('click')
    await wrapper.find('[data-testid="create-value-category-label"]').setValue('!!!')
    await wrapper.find('[data-testid="create-value-category-key"]').setValue('x')
    await wrapper.find('[data-testid="create-value-category-save"]').trigger('click')
    await flushPromises()
    expect(wrapper.find('[data-testid="create-value-category-error"]').text())
      .toContain('nothing matching')
  })
})
```

- [ ] **Step 2: Run and watch them fail**

```bash
cd /Users/john/projects/syncthing/agent-lxc/library-4c/frontend && npm run test:unit -- FacetsPanel
```

- [ ] **Step 3: Implement**

Structure: one `card p-6 @container` per facet, ordinal-ordered, holding a heading (`{{ facet.label }}` + `<code>{{ facet.key }}</code>`) and its value rows. Above them a "Create a facet" form; inside each facet card a "Add a value" control.

State to hold: `vocabulary`, `moneyCounts` and `labelCounts` as `Map`s keyed `` `${facet}:${value}` ``, `loading`, `error`, a per-row `rowError` record, per-row pending set, and at most one row each in rename / alias / delete-confirm mode — the shape `TaxonomyCrudPanel.vue` already uses; read it and follow it, but do **not** try to drive this panel through `TaxonomyDescriptor`: it is a flat, id-keyed, rename-collision-triggers-merge, delete-with-reassign contract, and none of those four things is true here.

Lazy load exactly as `AdminMetadataPanel` does — `watch(() => props.active, ...)` with a `loaded` flag, loading on the first `false → true` transition. Load the three GETs with `Promise.all`.

Key slugification for the create-value key field, mirroring the server's `derive_value_key` for convenience only (the server remains the judge and its 422 is rendered). **Create it as `frontend/src/utils/slugify.ts` with its own unit test** — Task 9 imports the same function, and a copy would be a second copy of a normalisation rule:

```ts
export function slugify(label: string): string {
  return label
    .trim()
    .toLowerCase()
    .replace(/ /g, '-')
    .replace(/[^a-z0-9_-]+/g, '')
    .replace(/([_-])\1+/g, '$1')
    .replace(/^[-_]+|[-_]+$/g, '')
    .slice(0, 64)
    .replace(/^[-_]+|[-_]+$/g, '')
}
```

Watch the label input and write `slugify(label)` into the key field only while the user has not edited the key themselves (track a `keyTouched` flag, set on the key input's own `input` event).

Collision marking: for each facet, compute a `Map<string, string[]>` from resolved colour (via `resolveSplitColour(value.colour, value.key, isDark)`) to value keys, and render the `value-{facet}-{value}-collision` element for any value whose bucket has more than one member. For `isDark`, read the app's current theme the way other components do — grep for how `dark` is determined elsewhere (`document.documentElement.classList.contains('dark')` or the appearance store) and reuse that; do not invent a second mechanism.

Row rendering, with `@container` breakpoints on the facet card: below `@md` the row stacks (swatch + label on one line, counts on the next, actions on a third); at `@md` and above it becomes a single line. Counts render as `{{ labelled }} labelled · {{ documents }} in charts`.

After any successful mutation, reload the three GETs (`await load()`), so counts and aliases stay truthful rather than being patched locally.

Error rendering: catch `ApiError` and set `rowError[key] = err.detail` — **verbatim**, not re-worded.

- [ ] **Step 4: Run and watch them pass**

```bash
cd /Users/john/projects/syncthing/agent-lxc/library-4c/frontend && npm run test:unit -- FacetsPanel && npm run type-check && npm run lint
```

- [ ] **Step 5: Mutation-check**

1. Render only `documents` and drop `labelled` → the both-counts test must fail.
2. Remove the client-side alias pre-check → the phantom-alias test must fail.
3. Replace `err.detail` with `'Could not delete'` → the 409-reason test must fail.
4. Compare raw `value.colour` instead of the resolved colour when bucketing collisions → the collision test must fail (the two values share a stored colour, so make the mutation compare `value.key` instead, which must also fail it).

Restore after each. Record the failure text.

- [ ] **Step 6: Prove the container query in a browser**

```bash
cd /Users/john/projects/syncthing/agent-lxc/library-4c/frontend && npm run dev
```

With the Playwright MCP tools or Chrome automation, load `/vocabulary` at viewport widths 375, 656 and 1280, **with the sidebar in both its collapsed and expanded states at 1280**, and confirm the row layout switches on the *card's* width, not the viewport's. Then prove the guard: temporarily change `@md:` to `md:` and confirm the layout breaks at one of those widths (the whole point — the viewport is wider than the column). Restore. Record what you observed at each width in the task report; a guard not seen to fail is not trusted.

- [ ] **Step 7: Commit**

```bash
cd /Users/john/projects/syncthing/agent-lxc/library-4c && git add -A && git commit -m "feat(vocabulary): the facets panel — counts, colour, rename, alias, create, delete"
```

---

### Task 7: The merge confirmation page

**Files:**
- Create: `frontend/src/views/vocabulary/ValueMergeView.vue`
- Modify: `frontend/src/router/index.ts`
- Modify: `frontend/src/views/vocabulary/FacetsPanel.vue` (the Merge action links to the page)
- Test: `frontend/src/views/vocabulary/__tests__/ValueMergeView.spec.ts`

**Interfaces:**
- Consumes: `fetchFacets`, `mergeValue` (Task 3).
- Produces: route name `vocabulary-merge` at `/vocabulary/:facetKey/:valueKey/merge`.

A page with its own URL rather than a modal, following the convention `router/index.ts` states on the document-delete route: destructive actions get a confirmation page, back-button friendly, never a JS-only modal.

- [ ] **Step 1: Write the failing tests**

```ts
import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import ValueMergeView from '../ValueMergeView.vue'
import { SPLIT_PALETTE } from '@/utils/splitPalette'

vi.mock('@/api/facets', () => ({ fetchFacets: vi.fn(), mergeValue: vi.fn() }))
import * as api from '@/api/facets'

const push = vi.fn()
vi.mock('vue-router', () => ({
  useRoute: () => ({ params: { facetKey: 'category', valueKey: 'alpha' } }),
  useRouter: () => ({ push }),
  RouterLink: { template: '<a><slot /></a>' },
}))

beforeEach(() => {
  push.mockClear()
  vi.mocked(api.fetchFacets).mockResolvedValue([
    {
      key: 'category', label: 'Category', ordinal: 0,
      values: [
        { key: 'alpha', label: 'Alpha', parent_id: null, aliases: ['a-one', 'shared'],
          colour: SPLIT_PALETTE[3].light },
        { key: 'beta', label: 'Beta', parent_id: null, aliases: ['shared'], colour: null },
        { key: 'gamma', label: 'Gamma', parent_id: null, aliases: [], colour: null },
      ],
    },
  ])
  vi.mocked(api.mergeValue).mockResolvedValue({ moved: 7 })
})

const open = async () => {
  const wrapper = mount(ValueMergeView)
  await flushPromises()
  return wrapper
}

const chooseTarget = async (wrapper: ReturnType<typeof mount>, value: string) => {
  await wrapper.find('[data-testid="merge-target"]').setValue(value)
  await flushPromises()
}

describe('ValueMergeView', () => {
  it('cannot apply before a preview has been run', async () => {
    const wrapper = await open()
    expect(
      (wrapper.find('[data-testid="merge-apply"]').element as HTMLButtonElement).disabled,
    ).toBe(true)
  })

  it('runs a dry run when a target is chosen and shows the count', async () => {
    const wrapper = await open()
    await chooseTarget(wrapper, 'beta')
    expect(api.mergeValue).toHaveBeenCalledWith('category', 'alpha', 'beta', true)
    expect(wrapper.find('[data-testid="merge-diff"]').text()).toContain('7')
    expect(
      (wrapper.find('[data-testid="merge-apply"]').element as HTMLButtonElement).disabled,
    ).toBe(false)
  })

  it('never offers the source as its own target', async () => {
    const wrapper = await open()
    const options = wrapper.findAll('[data-testid="merge-target"] option').map((o) => o.element.getAttribute('value'))
    expect(options).not.toContain('alpha')
    expect(options).toContain('beta')
    expect(options).toContain('gamma')
  })

  it('invalidates the preview when the target changes', async () => {
    // Otherwise the page shows a count computed for one target beside an Apply
    // button that merges into another — a preview that is worse than none.
    const wrapper = await open()
    await chooseTarget(wrapper, 'beta')
    await chooseTarget(wrapper, 'gamma')
    // Between the change and the new dry run resolving, apply must be off; and
    // the shown count must belong to the current target once it resolves.
    expect(api.mergeValue).toHaveBeenLastCalledWith('category', 'alpha', 'gamma', true)
  })

  it('disables apply the moment the target changes, before the new preview lands', async () => {
    const wrapper = await open()
    await chooseTarget(wrapper, 'beta')
    let resolve!: (v: { moved: number }) => void
    vi.mocked(api.mergeValue).mockReturnValueOnce(new Promise((r) => { resolve = r }))
    await wrapper.find('[data-testid="merge-target"]').setValue('gamma')
    expect(
      (wrapper.find('[data-testid="merge-apply"]').element as HTMLButtonElement).disabled,
    ).toBe(true)
    resolve({ moved: 2 })
    await flushPromises()
    expect(
      (wrapper.find('[data-testid="merge-apply"]').element as HTMLButtonElement).disabled,
    ).toBe(false)
  })

  it('shows which aliases the target gains and which it already has', async () => {
    const wrapper = await open()
    await chooseTarget(wrapper, 'beta')
    const diff = wrapper.find('[data-testid="merge-diff"]').text()
    expect(diff).toContain('alpha')     // the source key becomes an alias
    expect(diff).toContain('a-one')     // moves across
    expect(diff).toContain('shared')    // already on the target
  })

  it("warns that the source's colour override is destroyed", async () => {
    // Invisible in the API's answer and irreversible.
    const wrapper = await open()
    await chooseTarget(wrapper, 'beta')
    expect(wrapper.find('[data-testid="merge-colour-loss"]').exists()).toBe(true)
  })

  it('says nothing about colour when the source has no override', async () => {
    vi.mocked(api.fetchFacets).mockResolvedValue([
      {
        key: 'category', label: 'Category', ordinal: 0,
        values: [
          { key: 'alpha', label: 'Alpha', parent_id: null, aliases: [], colour: null },
          { key: 'beta', label: 'Beta', parent_id: null, aliases: [], colour: null },
        ],
      },
    ])
    const wrapper = await open()
    await chooseTarget(wrapper, 'beta')
    expect(wrapper.find('[data-testid="merge-colour-loss"]').exists()).toBe(false)
  })

  it('applies the merge with dry_run false and returns to the panel', async () => {
    const wrapper = await open()
    await chooseTarget(wrapper, 'beta')
    await wrapper.find('[data-testid="merge-apply"]').trigger('click')
    await flushPromises()
    expect(api.mergeValue).toHaveBeenLastCalledWith('category', 'alpha', 'beta', false)
    expect(push).toHaveBeenCalledWith({ name: 'vocabulary' })
  })
})
```

- [ ] **Step 2: Run and watch them fail**

```bash
cd /Users/john/projects/syncthing/agent-lxc/library-4c/frontend && npm run test:unit -- ValueMergeView
```

- [ ] **Step 3: Implement**

Load the vocabulary on mount, find the facet and the source value, and build the target `<select>` from every *other* value in the facet (so the self-merge 409 is unreachable; still catch it, since the route can answer it).

State: `target` (string), `moved` (number | null), `previewFor` (string | null — the target the current `moved` belongs to), `previewing`, `applying`, `error`.

The invalidation is the load-bearing part:

```ts
watch(target, async (next) => {
  moved.value = null
  previewFor.value = null          // apply is off from this instant
  error.value = null
  if (!next) return
  previewing.value = true
  try {
    const result = await mergeValue(facetKey, valueKey, next, true)
    if (target.value !== next) return   // a later change already superseded this
    moved.value = result.moved
    previewFor.value = next
  } catch (err) {
    error.value = err instanceof ApiError ? err.detail : 'Could not preview the merge.'
  } finally {
    previewing.value = false
  }
})

const canApply = computed(() => previewFor.value !== null && previewFor.value === target.value)
```

Diff rendering, in `[data-testid="merge-diff"]`, as a list of changes and not a sentence:
- `~ {{ moved }} documents relabelled — {{ source.label }} → {{ targetValue.label }}`
- `+ gains alias "{{ source.key }}"`
- one `+ gains alias "{{ alias }}"` per source alias the target lacks
- one `= already has alias "{{ alias }}"` per source alias the target has
- `− {{ source.label }} is deleted`
- when `source.colour` is non-null, `− its colour override is lost` in `[data-testid="merge-colour-loss"]`

Apply calls `mergeValue(facetKey, valueKey, target, false)` then `router.push({ name: 'vocabulary' })`.

Route, after the `/vocabulary` entry:

```ts
  {
    // GOV.UK pattern, as on document-delete: a destructive, irreversible action
    // gets a confirmation PAGE with its own URL, never a modal.
    path: '/vocabulary/:facetKey/:valueKey/merge',
    name: 'vocabulary-merge',
    component: () => import('../views/vocabulary/ValueMergeView.vue'),
  },
```

In `FacetsPanel.vue`, the Merge action is a `RouterLink` to `{ name: 'vocabulary-merge', params: { facetKey, valueKey } }` styled as a button, with testid `value-{facet}-{value}-merge-btn`.

- [ ] **Step 4: Run and watch them pass**

```bash
cd /Users/john/projects/syncthing/agent-lxc/library-4c/frontend && npm run test:unit -- ValueMergeView && npm run test:unit && npm run type-check && npm run lint
```

- [ ] **Step 5: Mutation-check**

1. Delete `previewFor.value = null` from the top of the watcher → the disables-on-change test must fail.
2. Change `canApply` to `moved.value !== null` → the same test must fail.
3. Remove the `if (target.value !== next) return` stale-response guard, then in a scratch test resolve two dry runs out of order → confirm the count can attach to the wrong target. (If this is awkward to drive, state so in the report rather than skipping silently.)
4. Always render `merge-colour-loss` → the no-override test must fail.

Restore. Record the failure text.

- [ ] **Step 6: Commit**

```bash
cd /Users/john/projects/syncthing/agent-lxc/library-4c && git add -A && git commit -m "feat(vocabulary): the merge confirmation page and its dry-run diff"
```

---

### Task 8: The Senders panel

**Files:**
- Modify: `frontend/src/views/vocabulary/SendersPanel.vue` (replace the stub)
- Test: `frontend/src/views/vocabulary/__tests__/SendersPanel.spec.ts`

**Interfaces:**
- Consumes: `listSenders`, `setSenderColour` (Task 3); `SplitColourPicker` (Task 4); `resolveSplitColour` (Task 2).

Colour only. A sender's name comes from ingested documents; renaming, merging and deleting one are admin taxonomy operations with their own panel.

- [ ] **Step 1: Write the failing tests**

```ts
import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import SendersPanel from '../SendersPanel.vue'
import { SPLIT_PALETTE } from '@/utils/splitPalette'

vi.mock('@/api/taxonomy', () => ({ listSenders: vi.fn(), setSenderColour: vi.fn() }))
import * as api from '@/api/taxonomy'

beforeEach(() => {
  vi.mocked(api.listSenders).mockResolvedValue([
    { id: 1, name: 'Aardvark Testing Ltd', document_count: 3, colour: null },
    { id: 2, name: 'Zebra Fixture Co', document_count: 11, colour: SPLIT_PALETTE[0].light },
  ])
})

const open = async () => {
  const wrapper = mount(SendersPanel, {
    props: { active: true },
    global: { stubs: { SplitColourPicker: true } },
  })
  await flushPromises()
  return wrapper
}

describe('SendersPanel', () => {
  it('does not load until its tab is opened', async () => {
    mount(SendersPanel, { props: { active: false }, global: { stubs: { SplitColourPicker: true } } })
    await flushPromises()
    expect(api.listSenders).not.toHaveBeenCalled()
  })

  it('lists the busiest senders first, since those are the ones charts split by', async () => {
    const wrapper = await open()
    const names = wrapper.findAll('[data-testid^="sender-row-"]').map((r) => r.text())
    expect(names[0]).toContain('Zebra Fixture Co')
  })

  it('filters by name', async () => {
    const wrapper = await open()
    await wrapper.find('[data-testid="sender-filter"]').setValue('aardvark')
    await flushPromises()
    const rows = wrapper.findAll('[data-testid^="sender-row-"]')
    expect(rows).toHaveLength(1)
    expect(rows[0].text()).toContain('Aardvark')
  })

  it('sets a colour', async () => {
    vi.mocked(api.setSenderColour).mockResolvedValue({
      id: 1, name: 'Aardvark Testing Ltd', document_count: 3, colour: SPLIT_PALETTE[2].light,
    })
    const wrapper = await open()
    wrapper.findAllComponents({ name: 'SplitColourPicker' })[1]
      .vm.$emit('update:modelValue', SPLIT_PALETTE[2].light)
    await flushPromises()
    expect(api.setSenderColour).toHaveBeenCalledWith(1, SPLIT_PALETTE[2].light)
  })

  it('clears a colour', async () => {
    vi.mocked(api.setSenderColour).mockResolvedValue({
      id: 2, name: 'Zebra Fixture Co', document_count: 11, colour: null,
    })
    const wrapper = await open()
    wrapper.findAllComponents({ name: 'SplitColourPicker' })[0]
      .vm.$emit('update:modelValue', null)
    await flushPromises()
    expect(api.setSenderColour).toHaveBeenCalledWith(2, null)
  })

  it('offers no rename or delete — those are admin taxonomy operations', async () => {
    const wrapper = await open()
    expect(wrapper.find('[data-testid="sender-row-1-rename-btn"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="sender-row-1-delete-btn"]').exists()).toBe(false)
  })
})
```

- [ ] **Step 2: Run and watch them fail**

```bash
cd /Users/john/projects/syncthing/agent-lxc/library-4c/frontend && npm run test:unit -- SendersPanel
```

- [ ] **Step 3: Implement**

Lazy load on `active`. Sort by `document_count` descending then name — the chart-relevant senders first, since `/api/senders` returns every sender ever ingested. A filter input (`sender-filter`) narrows by case-insensitive substring on the name. Each row (`sender-row-{id}`) is a swatch picker (`slotKey` is `String(sender.id)`, matching the derived-slot-from-id rule for senders in charts-view spec §2.5), the name, and the document count. `@container` again for the row layout.

Note in a comment at the top of the file why there is no rename or delete here.

- [ ] **Step 4: Run and watch them pass, then mutation-check**

```bash
cd /Users/john/projects/syncthing/agent-lxc/library-4c/frontend && npm run test:unit -- SendersPanel && npm run type-check && npm run lint
```

Mutations: sort ascending → the ordering test fails; drop the filter's `toLowerCase()` → the filter test fails (it searches `'aardvark'` against `'Aardvark…'`). Restore; record.

- [ ] **Step 5: Commit**

```bash
cd /Users/john/projects/syncthing/agent-lxc/library-4c && git add -A && git commit -m "feat(vocabulary): sender split colours"
```

---

### Task 9: The Suggestions panel

**Files:**
- Modify: `frontend/src/views/vocabulary/SuggestionsPanel.vue` (replace the stub)
- Test: `frontend/src/views/vocabulary/__tests__/SuggestionsPanel.spec.ts`

**Interfaces:**
- Consumes: `listSuggestions`, `acceptSuggestion`, `dismissSuggestion` (Task 3).

`accept` is the only sanctioned path that widens the vocabulary: it derives a clean key, creates the value, and labels the originating document in one call.

- [ ] **Step 1: Write the failing tests**

```ts
import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import SuggestionsPanel from '../SuggestionsPanel.vue'
import { ApiError } from '@/api/client'

vi.mock('@/api/facets', () => ({
  listSuggestions: vi.fn(), acceptSuggestion: vi.fn(), dismissSuggestion: vi.fn(),
}))
import * as api from '@/api/facets'

beforeEach(() => {
  vi.mocked(api.listSuggestions).mockResolvedValue([
    { id: 5, facet: 'category', suggested_label: 'Boat mooring',
      reason: 'the document is a mooring invoice', document_id: 42 },
  ])
})

const open = async () => {
  const wrapper = mount(SuggestionsPanel, {
    props: { active: true },
    global: { stubs: { RouterLink: { template: '<a><slot /></a>' } } },
  })
  await flushPromises()
  return wrapper
}

describe('SuggestionsPanel', () => {
  it('does not load until its tab is opened', async () => {
    mount(SuggestionsPanel, { props: { active: false } })
    await flushPromises()
    expect(api.listSuggestions).not.toHaveBeenCalled()
  })

  it('shows the facet, the label, the reason and a link to the document', async () => {
    const wrapper = await open()
    const row = wrapper.find('[data-testid="suggestion-5"]')
    expect(row.text()).toContain('category')
    expect(row.text()).toContain('Boat mooring')
    expect(row.text()).toContain('mooring invoice')
    expect(row.find('[data-testid="suggestion-5-document"]').exists()).toBe(true)
  })

  it('shows the key it will create before creating it', async () => {
    // Accept both widens the vocabulary and labels a document; the owner should
    // see the key that is about to enter the closed set.
    const wrapper = await open()
    expect(wrapper.find('[data-testid="suggestion-5-key"]').text()).toContain('boat-mooring')
  })

  it('accepts a suggestion', async () => {
    vi.mocked(api.acceptSuggestion).mockResolvedValue({ facet: 'category', value: 'boat-mooring' })
    const wrapper = await open()
    await wrapper.find('[data-testid="suggestion-5-accept"]').trigger('click')
    await flushPromises()
    expect(api.acceptSuggestion).toHaveBeenCalledWith(5)
  })

  it('dismisses a suggestion', async () => {
    vi.mocked(api.dismissSuggestion).mockResolvedValue({ state: 'dismissed' })
    const wrapper = await open()
    await wrapper.find('[data-testid="suggestion-5-dismiss"]').trigger('click')
    await flushPromises()
    expect(api.dismissSuggestion).toHaveBeenCalledWith(5)
  })

  it("renders the server's 409 when the derived key already exists", async () => {
    vi.mocked(api.acceptSuggestion).mockRejectedValue(
      new ApiError(409, 'category=boat-mooring already exists', {}),
    )
    const wrapper = await open()
    await wrapper.find('[data-testid="suggestion-5-accept"]').trigger('click')
    await flushPromises()
    expect(wrapper.find('[data-testid="suggestion-5-error"]').text())
      .toContain('category=boat-mooring already exists')
  })

  it('says so plainly when the queue is empty', async () => {
    vi.mocked(api.listSuggestions).mockResolvedValue([])
    const wrapper = await open()
    expect(wrapper.find('[data-testid="suggestions-empty"]').exists()).toBe(true)
  })
})
```

- [ ] **Step 2: Run and watch them fail, then implement**

```bash
cd /Users/john/projects/syncthing/agent-lxc/library-4c/frontend && npm run test:unit -- SuggestionsPanel
```

Lazy load on `active`. One row per suggestion (`suggestion-{id}`) showing the facet key, the suggested label, the derived key (import `slugify` from `@/utils/slugify`, created in Task 6 — do not copy it), the reason, a `RouterLink` to `{ name: 'document-detail', params: { id: document_id } }`, and Accept / Dismiss buttons. Reload the list after either action. Empty state at `suggestions-empty`.

- [ ] **Step 3: Run, mutation-check, commit**

Mutations: render `suggested_label` where the derived key belongs → the key test fails; swallow the error into a generic message → the 409 test fails. Restore; record.

```bash
cd /Users/john/projects/syncthing/agent-lxc/library-4c/frontend && npm run test:unit && npm run type-check && npm run lint
cd /Users/john/projects/syncthing/agent-lxc/library-4c && git add -A && git commit -m "feat(vocabulary): the labeller's pending suggestion queue"
```

---

### Task 10: End-to-end journey

**Files:**
- Create: `frontend/e2e/vocabulary.spec.ts`

**Interfaces:**
- Consumes: the whole view.

Read `frontend/e2e/facets.spec.ts` first and copy its shape exactly: the `requireStack()` env self-skip, the `signIn` helper, the API-seeding trick with the CSRF cookie, and the **unique-key-per-run rule** — this backend is shared serially across browser projects, so derive the facet key from `Date.now()` and the project name.

Nothing here may assert on layout: the three projects are 1280, 656 and 375 wide and the panel reflows. Assert presence, text, values and counts only.

- [ ] **Step 1: Write the spec**

One test that walks the whole journey, so a single run exercises every write route:

1. sign in, go to `/vocabulary`;
2. create a facet with a run-unique key;
3. create two values in it, `alpha-<run>` and `beta-<run>` — **invented names only, never a real vehicle, address or person**;
4. rename the first value's label, assert the new label renders;
5. add an alias to it, assert the alias renders; add the same alias again and assert the panel says it is already an alias and the row does not gain a duplicate;
6. set a colour on it from the picker, reload the page, assert it is still selected (proving the write persisted, not just the local state);
7. clear the colour, reload, assert the default is selected again;
8. seed a document via the API and label it with the first value (`PUT /api/documents/{id}/labels`), reload, assert the row reads `1 labelled`;
9. click Merge on the first value, choose the second as target, assert the diff shows `1` and names both aliases, then apply; assert the first value is gone and the second reads `1 labelled`;
10. delete the second value and assert the 409 text names the document count; unlabel the document (`PUT` with `null`), then delete it successfully and assert the row disappears;
11. clean up: delete the seeded document.

Use `page.getByTestId(...)` throughout, matching the testids the earlier tasks defined.

- [ ] **Step 2: Run it on all three projects**

```bash
cd /Users/john/projects/syncthing/agent-lxc/library-4c/frontend && npm run test:e2e -- vocabulary
```
Expected: PASS on chromium@1280, mobile-webkit@375 and tablet-webkit@656. If it needs the stack, follow whatever `requireStack()` documents.

- [ ] **Step 3: Run the whole e2e suite**

```bash
cd /Users/john/projects/syncthing/agent-lxc/library-4c/frontend && npm run test:e2e
```
Expected: no regressions. A new sidebar entry changes the sidebar's height and can break another spec's assumptions — this run is what catches that.

- [ ] **Step 4: Commit**

```bash
cd /Users/john/projects/syncthing/agent-lxc/library-4c && git add -A && git commit -m "test(vocabulary): the end-to-end vocabulary journey"
```

---

### Task 11: Documentation and the journal

**Files:**
- Modify: `docs/facets.md`
- Modify: `docs/api.md`
- Modify: `docs/frontend.md`
- Create: `journal/260830-facet-vocabulary-panel.md`

- [ ] **Step 1: `docs/api.md`**

Add a `GET /api/facets/label-counts` row to the §1.1 endpoint summary and to §1.23's route table, and a new §1.23.6 documenting it: the response shape, and — the part that matters — **why it is a separate route from §1.23.5 rather than a field on it**, naming the three ways `spend_facts` and `document_labels` diverge and stating that this route's number is the one `DELETE .../values/{value}` enforces.

Update the `**Status:**`/`**Last updated:**`/`**Last verified:**` stamps at the top in the established format: what changed, and for "Last verified" the *method* — which source files you read, which assertions cover each claim, and the mutation you ran and observed. Do not claim a mutation you did not run.

- [ ] **Step 2: `docs/facets.md`**

- §4's cost table: add a row for the panel where each operation is now performed.
- §6: add `GET /api/facets/label-counts` to the REST surface paragraph, with one sentence on how it differs from `/api/facets/counts`.
- New §8, "The vocabulary panel": the `/vocabulary` route, its three tabs, that merge previews before it applies and delete renders the server's reason, that colour is restricted to a validated six-slot palette with a null-means-derived default, and that a new facet carries no documents until `label-archive` runs.
- Update the stamps as in Step 1.

- [ ] **Step 3: `docs/frontend.md`**

Add the view to whatever section enumerates routes/views (find it first — the file is 123k; `grep -n "^## " docs/frontend.md` and pick the right one). Cover the tab shell, the lazy per-tab load, the merge page's target-specific approval, and the `@container` rule for the value row with a pointer to §5.1's canonical statement of the breakpoint hazard. Update the stamps.

- [ ] **Step 4: The journal entry**

`journal/260830-facet-vocabulary-panel.md`, H1 a clean title with no number or date. Cover: what shipped; the counts discovery and why it became a separate route rather than a field (including that the first design would have broken `test_a_value_with_no_money_behind_it_is_absent` and changed 4b's empty state); the palette validation, including that the eight-hue reference set fails all-pairs and why all-pairs is the right list when slots are hash-derived; the two-narrow-functions decision for absent-versus-null; the second copy deleted in `delete_value`; and every mutation check run, with the observed failure. No real values anywhere.

- [ ] **Step 5: Run the docs gates**

```bash
cd /Users/john/projects/syncthing/agent-lxc/library-4c && uv run python scripts/check_docs.py && uv run python scripts/build_journal_index.py --check && uv run python scripts/build_journal_index.py
```
Expected: clean. If `build_journal_index.py --check` fails, run it without `--check` to regenerate the index, then commit the result.

- [ ] **Step 6: Commit**

```bash
cd /Users/john/projects/syncthing/agent-lxc/library-4c && git add -A && git commit -m "docs(vocabulary): the vocabulary panel, the label-count route and the journal entry"
```

---

## Final verification before the PR

- [ ] `cd frontend && npm run test:unit` — green
- [ ] `cd frontend && npm run lint` — green
- [ ] `cd frontend && npm run type-check` — green
- [ ] `cd frontend && npm run test:e2e` — green on all three viewport projects
- [ ] `uv run pytest` — full backend suite green (not just the facet files)
- [ ] `uv run ruff format --check .` and `make lint` — green
- [ ] `uv run python scripts/check_docs.py` — green
- [ ] Every mutation check in every task actually run and its failure observed, recorded in the task reports
- [ ] No real facet value, sender, address, registration, person or amount anywhere in the diff — `git diff main --stat` then read the fixtures

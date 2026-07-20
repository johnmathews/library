# Phone Columns + Labeled Tile Date Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the dashboard phone-column count a server-synced Appearance setting (default 2) and label the primary document date on tiles so metadata is always key: value.

**Architecture:** The column count is a new `phone_columns` key in the existing `user.preferences` JSON blob — no DB migration. It flows through the appearance PUT, the settings read model, the auth store, a SettingsView control, and finally a `--doc-grid-cols-phone` CSS var on the tile grid. The date label is a one-line template change on the dashboard tile.

**Tech Stack:** FastAPI + Pydantic + SQLAlchemy (async) backend; Vue 3 + Pinia + Tailwind frontend; pytest (backend) and Vitest/@vue/test-utils (frontend).

## Global Constraints

- Python 3.13, type annotations on all signatures; `uv run` for commands; `pytest`.
- Preferences are a tolerant JSON blob: unknown/garbage values resolve to the default, never a 422 — mirror the existing `_resolve_tile_preview` / `_default_unknown_tile_preview` pattern.
- `phone_columns` allowed set is `{1, 2, 3}`; default is `2`.
- CI runs `ruff check` and `ruff format --check` over the WHOLE repo — format touched files before committing.
- Frontend `UserPreferences` fields are optional on the client (older payloads/fixtures must still type-check); consumers fall back to defaults.
- The muted date prefix uses no colon, matching the existing secondary-date prefixes (Added / Due / Expires / Edited).

---

### Task 1: Backend `phone_columns` preference

**Files:**
- Modify: `src/library/schemas.py` (near `_resolve_tile_preview` ~506, `AppearancePreferences` ~544, `UserPreferences` ~797, `resolve_preferences` ~814)
- Modify: `src/library/api/settings.py:192-197` (`put_appearance` blob)
- Test: `tests/test_settings_api.py`

**Interfaces:**
- Produces: `DEFAULT_PHONE_COLUMNS: int = 2`, `_resolve_phone_columns(blob: dict[str, Any]) -> int`; `AppearancePreferences.phone_columns: int`; `UserPreferences.phone_columns: int`. GET `/api/settings` and PUT `/api/settings/appearance` responses gain a `phone_columns` int field.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_settings_api.py`:

```python
def test_get_settings_includes_default_phone_columns(api_client: TestClient) -> None:
    assert api_client.get("/api/settings").json()["phone_columns"] == 2


def test_put_appearance_round_trips_phone_columns(api_client: TestClient) -> None:
    put = api_client.put(
        "/api/settings/appearance",
        json={"background_tone": "neutral", "phone_columns": 3},
    )
    assert put.status_code == 200, put.text
    assert put.json()["phone_columns"] == 3
    assert api_client.get("/api/settings").json()["phone_columns"] == 3


def test_put_appearance_out_of_range_phone_columns_falls_back_to_default(
    api_client: TestClient,
) -> None:
    put = api_client.put(
        "/api/settings/appearance",
        json={"background_tone": "neutral", "phone_columns": 9},
    )
    assert put.status_code == 200, put.text
    assert put.json()["phone_columns"] == 2


def test_get_settings_resolves_garbage_phone_columns_to_default(
    api_client: TestClient, auth_user: AuthUser, api_database_url: str
) -> None:
    _seed_raw_preferences(api_database_url, auth_user.id, {"phone_columns": "lots"})
    assert api_client.get("/api/settings").json()["phone_columns"] == 2
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_settings_api.py -k phone_columns -v`
Expected: FAIL — response JSON has no `phone_columns` key (KeyError / assertion error).

- [ ] **Step 3: Add the resolver and constant in `schemas.py`**

After the `_resolve_tile_preview` block (~line 511), add:

```python
DEFAULT_PHONE_COLUMNS: Final[int] = 2
_ALLOWED_PHONE_COLUMNS: Final[frozenset[int]] = frozenset({1, 2, 3})


def _coerce_phone_columns(value: object) -> int:
    """Coerce any stored/submitted value to an allowed column count.

    Tolerant like :func:`_resolve_tile_preview`: a non-int, an out-of-range
    number, or a hand-edited string resolves to the default rather than raising.
    """
    if isinstance(value, bool):  # bool is an int subclass — reject it explicitly
        return DEFAULT_PHONE_COLUMNS
    if isinstance(value, int) and value in _ALLOWED_PHONE_COLUMNS:
        return value
    return DEFAULT_PHONE_COLUMNS


def _resolve_phone_columns(blob: dict[str, Any]) -> int:
    """Pick the stored phone column count, falling back for absent/garbage values."""
    return _coerce_phone_columns(blob.get("phone_columns"))
```

- [ ] **Step 4: Add the request field + validator to `AppearancePreferences`**

In `AppearancePreferences` (add the field after `dock_position` ~line 549, and the validator after `_default_unknown_dock_position` ~line 573):

```python
    phone_columns: int = DEFAULT_PHONE_COLUMNS
```

```python
    @field_validator("phone_columns", mode="before")
    @classmethod
    def _default_out_of_range_phone_columns(cls, value: object) -> int:
        """Coerce an unknown/out-of-range column count to the default (never a 422)."""
        return _coerce_phone_columns(value)
```

- [ ] **Step 5: Add the read-model field and wire the resolver**

In `UserPreferences` (after `dock_position` ~line 809):

```python
    phone_columns: int
```

In `resolve_preferences` (in the `return UserPreferences(...)` call ~line 821):

```python
        phone_columns=_resolve_phone_columns(blob),
```

- [ ] **Step 6: Persist the field in `put_appearance`**

In `src/library/api/settings.py`, `put_appearance` (~line 192), add to the blob:

```python
        "phone_columns": payload.phone_columns,
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `uv run pytest tests/test_settings_api.py -k phone_columns -v`
Expected: PASS (4 tests).

- [ ] **Step 8: Format and commit**

```bash
uv run ruff format src/library/schemas.py src/library/api/settings.py tests/test_settings_api.py
uv run ruff check src/library/schemas.py src/library/api/settings.py tests/test_settings_api.py
git add src/library/schemas.py src/library/api/settings.py tests/test_settings_api.py
git commit -m "feat(settings): server-synced phone_columns appearance preference"
```

---

### Task 2: Frontend read model — types, constants, auth store

**Files:**
- Modify: `frontend/src/api/settings.ts` (`UserPreferences` ~181; add consts near the other appearance consts)
- Modify: `frontend/src/stores/auth.ts` (imports ~4-14; computed ~54; return block ~119)
- Test: `frontend/src/stores/__tests__/auth.spec.ts`

**Interfaces:**
- Consumes: `UserPreferences` gains an optional `phone_columns?: number` (Task 1 response).
- Produces: `PHONE_COLUMNS_OPTIONS: readonly [1, 2, 3]`, `DEFAULT_PHONE_COLUMNS = 2` (from `@/api/settings`); `auth.phoneColumns` computed returning a `number`.

- [ ] **Step 1: Write the failing auth-store test**

Append to `frontend/src/stores/__tests__/auth.spec.ts` (inside the existing `describe`, following the style of the `tilePreview`/`dockPosition` tests):

```ts
it('phoneColumns defaults to 2 when the preference is absent', () => {
  const auth = useAuthStore()
  auth.user = {
    id: 1,
    username: 'u',
    display_name: 'U',
    is_admin: false,
    preferences: { dashboard_fields: [] },
  }
  expect(auth.phoneColumns).toBe(2)
})

it('phoneColumns reflects the stored preference', () => {
  const auth = useAuthStore()
  auth.user = {
    id: 1,
    username: 'u',
    display_name: 'U',
    is_admin: false,
    preferences: { dashboard_fields: [], phone_columns: 3 },
  }
  expect(auth.phoneColumns).toBe(3)
})
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd frontend && npx vitest run src/stores/__tests__/auth.spec.ts -t phoneColumns`
Expected: FAIL — `auth.phoneColumns` is `undefined` (property does not exist).

- [ ] **Step 3: Add the type + constants in `api/settings.ts`**

Add the `phone_columns` field to `UserPreferences` (after `dock_position?` ~line 187):

```ts
  phone_columns?: number
```

Add near the other appearance constants (e.g. just below the `TILE_PREVIEWS` block):

```ts
/** Allowed dashboard column counts on phones (< 641px). Default is 2. */
export const PHONE_COLUMNS_OPTIONS = [1, 2, 3] as const
export const DEFAULT_PHONE_COLUMNS = 2
```

- [ ] **Step 4: Add the `phoneColumns` computed to the auth store**

In `frontend/src/stores/auth.ts`, extend the import from `@/api/settings` (~line 4) with:

```ts
  DEFAULT_PHONE_COLUMNS,
```

Add the computed after `dockPosition` (~line 62):

```ts
  // Dashboard columns on phones (< 641px). Server-synced; defaults when the
  // user is absent or a payload predates the preference.
  const phoneColumns = computed<number>(
    () => user.value?.preferences?.phone_columns ?? DEFAULT_PHONE_COLUMNS,
  )
```

Add `phoneColumns` to the returned object (~line 126, after `dockPosition`):

```ts
    phoneColumns,
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `cd frontend && npx vitest run src/stores/__tests__/auth.spec.ts -t phoneColumns`
Expected: PASS (2 tests).

- [ ] **Step 6: Typecheck and commit**

```bash
cd frontend && npx vue-tsc --noEmit
git add src/api/settings.ts src/stores/auth.ts src/stores/__tests__/auth.spec.ts
git commit -m "feat(settings): frontend phoneColumns read model + auth store"
```

---

### Task 3: `updateAppearance` param + SettingsView control

**Files:**
- Modify: `frontend/src/api/settings.ts:205-214` (`updateAppearance`)
- Modify: `frontend/src/views/SettingsView.vue` (imports ~21-29; appearance script ~102-148; all three `updateAppearance(...)` call sites at ~92, ~114, ~142; appearance template — new card after the dock-position card ~554)
- Test: `frontend/src/api/__tests__/settings.spec.ts:42-65`, `frontend/src/views/__tests__/SettingsView.spec.ts`

**Interfaces:**
- Consumes: `PHONE_COLUMNS_OPTIONS`, `DEFAULT_PHONE_COLUMNS`, `auth.phoneColumns` (Task 2).
- Produces: `updateAppearance(tone, tilePreview, dockPosition, phoneColumns)` — a required 4th `number` param serialized as `phone_columns`; a `selectPhoneColumns(n: number)` handler in SettingsView backed by a `settings-phone-columns` radiogroup with `phone-columns-{n}` buttons.

- [ ] **Step 1: Update the api test to expect the new arg**

In `frontend/src/api/__tests__/settings.spec.ts`, replace the appearance test body (~42-65) so the mocked response includes `phone_columns`, the call passes a 4th arg, and the asserted body includes `phone_columns`:

```ts
  it('PUT /api/settings/appearance sends tone, tile preview, dock position, and phone columns', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(
        jsonResponse({
          dashboard_fields: ['kind'],
          background_tone: 'slate',
          tile_preview: 'whole_page',
          dock_position: 'bottom-left',
          phone_columns: 3,
        }),
      )
    vi.stubGlobal('fetch', fetchMock)
    const result = await updateAppearance('slate', 'whole_page', 'bottom-left', 3)
    expect(result.phone_columns).toBe(3)
    const [url, init] = fetchMock.mock.calls[0]!
    expect(String(url)).toBe('/api/settings/appearance')
    expect(init.method).toBe('PUT')
    expect(JSON.parse(init.body)).toEqual({
      background_tone: 'slate',
      tile_preview: 'whole_page',
      dock_position: 'bottom-left',
      phone_columns: 3,
    })
  })
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd frontend && npx vitest run src/api/__tests__/settings.spec.ts -t "phone columns"`
Expected: FAIL — body lacks `phone_columns`; `updateAppearance` rejects a 4th arg (TS) / ignores it.

- [ ] **Step 3: Add the `phoneColumns` param to `updateAppearance`**

In `frontend/src/api/settings.ts`, replace `updateAppearance` (~205-214):

```ts
/** PUT /api/settings/appearance — persist tone, tile preview, dock position, and phone columns. */
export function updateAppearance(
  tone: BackgroundTone,
  tilePreview: TilePreview,
  dockPosition: DockPosition,
  phoneColumns: number,
): Promise<UserPreferences> {
  return apiFetch<UserPreferences>('/api/settings/appearance', {
    method: 'PUT',
    body: {
      background_tone: tone,
      tile_preview: tilePreview,
      dock_position: dockPosition,
      phone_columns: phoneColumns,
    },
  })
}
```

- [ ] **Step 4: Run the api test to verify it passes**

Run: `cd frontend && npx vitest run src/api/__tests__/settings.spec.ts -t "phone columns"`
Expected: PASS.

- [ ] **Step 5: Thread `phoneColumns` through the three existing call sites**

In `frontend/src/views/SettingsView.vue`, pass `auth.phoneColumns` as the 4th arg at each existing `updateAppearance(...)`:

- `selectTone` (~line 92): `await updateAppearance(tone, selectedTilePreview.value, auth.dockPosition, auth.phoneColumns)`
- `selectTilePreview` (~line 114): `await updateAppearance(selectedTone.value, mode, auth.dockPosition, auth.phoneColumns)`
- `selectDockPosition` (~line 142): `await updateAppearance(selectedTone.value, selectedTilePreview.value, position, auth.phoneColumns)`

- [ ] **Step 6: Add the phone-columns import + handler**

In the `@/api/settings` import block (~21-29) add:

```ts
  PHONE_COLUMNS_OPTIONS,
```

In the appearance script region (after `selectDockPosition`, ~line 148) add:

```ts
// --- Appearance (phone columns) ---------------------------------------------

async function selectPhoneColumns(count: number): Promise<void> {
  if (count === auth.phoneColumns) return
  const previous = auth.phoneColumns
  toneError.value = null
  // Optimistic: update the store now so the dashboard grid reflows immediately.
  if (auth.user) auth.applyPreferences({ ...auth.user.preferences, phone_columns: count })
  try {
    const result = await updateAppearance(
      selectedTone.value,
      selectedTilePreview.value,
      auth.dockPosition,
      count,
    )
    auth.applyPreferences(result)
  } catch {
    if (auth.user) auth.applyPreferences({ ...auth.user.preferences, phone_columns: previous })
    toneError.value = 'Sorry, your appearance preference could not be saved. Try again.'
  }
}
```

- [ ] **Step 7: Add the settings card to the appearance template**

In `frontend/src/views/SettingsView.vue`, after the dock-position card (closes ~line 554), add:

```vue
      <div id="settings-card-phone-columns" :class="cardClass" class="mt-6">
        <fieldset>
          <legend class="text-lg font-semibold text-gray-800 dark:text-gray-100">
            Phone columns
          </legend>
          <p class="text-sm text-gray-500 dark:text-gray-400 mt-1">
            How many dashboard tile columns to show on a phone-sized screen. Saves
            to your account automatically.
          </p>
          <div
            role="radiogroup"
            aria-label="Phone columns"
            data-testid="settings-phone-columns"
            class="grid grid-cols-3 gap-3 mt-5 max-w-xs"
          >
            <button
              v-for="count in PHONE_COLUMNS_OPTIONS"
              :key="count"
              type="button"
              role="radio"
              :aria-checked="auth.phoneColumns === count"
              :data-testid="`phone-columns-${count}`"
              :class="[
                'flex items-center justify-center rounded-lg border p-3 text-sm font-medium transition cursor-pointer',
                auth.phoneColumns === count
                  ? 'border-violet-500 ring-2 ring-violet-500/30 text-violet-600'
                  : 'border-gray-200 dark:border-gray-700/60 text-gray-700 dark:text-gray-200 hover:border-gray-300 dark:hover:border-gray-600',
              ]"
              @click="selectPhoneColumns(count)"
            >
              {{ count }}
            </button>
          </div>
        </fieldset>
      </div>
```

- [ ] **Step 8: Write the SettingsView test**

Append to `frontend/src/views/__tests__/SettingsView.spec.ts` (follow the existing dock-position/tile-preview test style — mount on the appearance tab, seed `auth.user`, mock `fetch`):

```ts
it('persists a phone-columns choice via the appearance endpoint', async () => {
  const fetchMock = vi
    .fn()
    .mockResolvedValue(
      jsonResponse({ dashboard_fields: [], phone_columns: 3 }),
    )
  vi.stubGlobal('fetch', fetchMock)
  const w = await mountAppearanceTab() // existing helper that opens the appearance tab
  await w.find('[data-testid="phone-columns-3"]').trigger('click')
  await flushPromises()
  const [url, init] = fetchMock.mock.calls.at(-1)!
  expect(String(url)).toBe('/api/settings/appearance')
  expect(JSON.parse(init.body).phone_columns).toBe(3)
})
```

> Note: use the appearance-tab mount/seed helper already present in this spec (search for how the dock-position test mounts). If none exists, mirror the tile-preview test's setup exactly.

- [ ] **Step 9: Run the SettingsView + api tests**

Run: `cd frontend && npx vitest run src/views/__tests__/SettingsView.spec.ts src/api/__tests__/settings.spec.ts`
Expected: PASS.

- [ ] **Step 10: Typecheck and commit**

```bash
cd frontend && npx vue-tsc --noEmit
git add src/api/settings.ts src/api/__tests__/settings.spec.ts src/views/SettingsView.vue src/views/__tests__/SettingsView.spec.ts
git commit -m "feat(settings): phone-columns control in the Appearance tab"
```

---

### Task 4: Apply the phone-column var + label the tile date

**Files:**
- Modify: `frontend/src/views/DocumentListView.vue` (`gridColsStyle` ~382-384; the `field === 'date'` span ~717-722)
- Modify: `frontend/src/assets/utility-patterns.css` (`.app-doc-grid` base rule ~209-213; comment ~205-207)
- Test: `frontend/src/views/__tests__/DocumentListView.spec.ts` (update ~231 and ~280; add new assertions)

**Interfaces:**
- Consumes: `auth.phoneColumns` (Task 2).
- Produces: `#dashboard-grid` always carries `--doc-grid-cols-phone: <n>`; the `.app-doc-card__date` span renders a muted `Date` prefix before the value and carries `data-testid="doc-date"`.

- [ ] **Step 1: Update the two existing tests and add coverage**

In `frontend/src/views/__tests__/DocumentListView.spec.ts`:

Change line ~231 from:

```ts
    expect(tile.find('.app-doc-card__date').text()).toBe('15 May 2026')
```

to:

```ts
    expect(tile.find('.app-doc-card__date').text()).toBe('Date 15 May 2026')
```

Change line ~280 from:

```ts
    expect(w.find('#dashboard-grid').attributes('style') ?? '').not.toContain('--doc-grid-cols')
```

to (the desktop override var is absent in Auto mode, but the phone var is always present):

```ts
    expect(w.find('#dashboard-grid').attributes('style') ?? '').not.toContain('--doc-grid-cols:')
```

Add two new tests (place near the existing date / grid tests):

```ts
it('labels the document date with a muted "Date" prefix', async () => {
  listResponse = () => jsonResponse(listBody([makeItem()]))
  const w = await mountView()
  const dateEl = w.find('[data-testid="doc-date"]')
  expect(dateEl.exists()).toBe(true)
  expect(dateEl.text()).toBe('Date 15 May 2026')
})

it('applies the phone-column count as the --doc-grid-cols-phone var', async () => {
  listResponse = () => jsonResponse(listBody([makeItem()]))
  const w = await mountView()
  // auth.phoneColumns defaults to 2 with no stored preference.
  expect(w.find('#dashboard-grid').attributes('style')).toContain('--doc-grid-cols-phone: 2')
})
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd frontend && npx vitest run src/views/__tests__/DocumentListView.spec.ts -t "date"`
Expected: FAIL — date text is `15 May 2026` (no prefix), no `doc-date` testid, style lacks `--doc-grid-cols-phone`.

- [ ] **Step 3: Bind the phone-column var on the grid**

In `frontend/src/views/DocumentListView.vue`, replace `gridColsStyle` (~382-384):

```ts
const gridColsStyle = computed<Record<string, string>>(() => ({
  '--doc-grid-cols-phone': String(auth.phoneColumns),
  ...(gridCols.value === 'auto' ? {} : { '--doc-grid-cols': gridCols.value }),
}))
```

- [ ] **Step 4: Add the muted `Date` prefix to the tile date**

Replace the `field === 'date'` span (~717-722):

```vue
              <span
                v-else-if="field === 'date' && item.document_date"
                class="app-doc-card__date text-sm text-gray-500 dark:text-gray-400"
                data-testid="doc-date"
              >
                <span class="text-gray-400 dark:text-gray-500">Date</span>
                {{ formatDate(item.document_date) }}
              </span>
```

- [ ] **Step 5: Make the CSS phone band honour the var**

In `frontend/src/assets/utility-patterns.css`, replace the base `.app-doc-grid` rule (~209-213):

```css
.app-doc-grid {
  display: grid;
  grid-template-columns: repeat(var(--doc-grid-cols-phone, 2), minmax(0, 1fr));
  gap: 1.5rem;
}
```

Update the explanatory comment above it (~205-207) so it no longer claims the phone band ignores overrides:

```css
 * the var falls back to 3/4 — i.e. the original desktop contract is preserved.
 * The phone band (< 641px) honours a separate --doc-grid-cols-phone var
 * (default 2, set from the account's phone_columns preference); the tablet
 * band (641–768px) stays fixed at 2 so tiles never get crushed.
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `cd frontend && npx vitest run src/views/__tests__/DocumentListView.spec.ts`
Expected: PASS (including the two updated tests and two new ones).

- [ ] **Step 7: Typecheck and commit**

```bash
cd frontend && npx vue-tsc --noEmit
git add src/views/DocumentListView.vue src/assets/utility-patterns.css src/views/__tests__/DocumentListView.spec.ts
git commit -m "feat(dashboard): configurable phone columns + labeled tile date"
```

---

### Task 5: Docs, journal, and full verification

**Files:**
- Modify: `docs/frontend.md` (dashboard tile fields + appearance settings sections)
- Create: `journal/260720-phone-columns-tile-date-label.md`

- [ ] **Step 1: Update `docs/frontend.md`**

Find the dashboard tile-fields / appearance-settings sections (grep for `tile_preview`, `dock_position`, `dashboard tile`). Document:
- The new `phone_columns` appearance preference (values 1/2/3, default 2, server-synced, applies to the `< 641px` band via `--doc-grid-cols-phone`).
- That the primary document date now renders with a muted `Date` prefix (metadata is key: value; amount stays bare, sender stays a plain name).

Keep it concise and match the surrounding doc style; do not leave placeholders.

- [ ] **Step 2: Write the journal entry**

Create `journal/260720-phone-columns-tile-date-label.md` capturing: the request, the decisions (Settings/Appearance server-synced, 1/2/3 default 2, date-only muted-prefix label), that no DB migration was needed (JSON blob key), and the default flip from 1 → 2 on phones for existing users. Use a clean H1 title (no number/date in the heading).

- [ ] **Step 3: Run the full backend + frontend suites**

```bash
uv run pytest tests/test_settings_api.py -v
uv run ruff format --check .
uv run ruff check .
cd frontend && npx vitest run && npx vue-tsc --noEmit
```

Expected: all green; ruff format reports no changes.

- [ ] **Step 4: Manually verify (per the verify skill)**

Run the app, shrink to a phone width (< 641px): tiles show 2 columns by default; Settings → Appearance → Phone columns switches between 1/2/3 and the dashboard reflows. Confirm a tile's document date reads `Date <value>`.

- [ ] **Step 5: Commit**

```bash
git add docs/frontend.md journal/260720-phone-columns-tile-date-label.md
git commit -m "docs: phone columns + labeled tile date"
```

---

## Self-Review

**Spec coverage:**
- Part A backend (schema/resolver/endpoint, no migration) → Task 1. ✅
- Part A frontend (api type, store, updateAppearance, SettingsView control, grid var, CSS) → Tasks 2–4. ✅
- Part B date label (muted prefix, date only, amount/sender unchanged) → Task 4. ✅
- Testing (backend clamp/default, store default/resolve, updateAppearance body, SettingsView persist, DocumentListView label + var) → Tasks 1–4. ✅
- Existing-test breakage (DocumentListView `.app-doc-card__date` text; the `--doc-grid-cols` Auto-mode assertion) → Task 4 Step 1. ✅
- Docs + journal → Task 5. ✅

**Placeholder scan:** SettingsView test references the spec's existing appearance-tab mount helper (Task 3 Step 8) — flagged with a fallback instruction rather than inventing a signature, since the exact helper name isn't known without reading that spec. No other placeholders.

**Type consistency:** `phone_columns` (snake_case, wire/JSON) vs `phoneColumns` (camelCase, store/param) used consistently; `DEFAULT_PHONE_COLUMNS` = 2 in both backend and frontend; `--doc-grid-cols-phone` var name matches between DocumentListView binding and the CSS rule.

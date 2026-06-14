# Document Tile Preview Mode Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a per-user "document tile preview" appearance setting that switches dashboard tiles between a full-width top crop (new default) and the whole-page letterbox (current behavior).

**Architecture:** New enum preference `tile_preview` stored in the user's JSONB `preferences`, resolved/persisted alongside `background_tone` through the existing `/settings/appearance` endpoint. It flows backend schema → API → frontend settings API → auth store → Settings UI, and is consumed directly on the dashboard thumbnail `<img>` (no global CSS).

**Tech Stack:** FastAPI + Pydantic + SQLAlchemy (JSONB) backend; Vue 3 `<script setup>` + Pinia + Tailwind frontend; pytest (backend) and Vitest + @vue/test-utils (frontend).

Spec: `docs/superpowers/specs/2026-06-14-tile-preview-mode-design.md`

---

## File structure

| File | Change | Responsibility |
| --- | --- | --- |
| `src/library/schemas.py` | modify | `TilePreview` enum, default, resolver, `UserPreferences` field |
| `src/library/api/settings.py` | modify | persist `tile_preview` in `PUT /settings/appearance` |
| `tests/test_settings_api.py` | modify | backend round-trip / default / independence tests |
| `frontend/src/api/settings.ts` | modify | `TILE_PREVIEWS`, `TilePreview`, default, `updateAppearance` signature, `UserPreferences` field |
| `frontend/src/api/__tests__/settings.spec.ts` | modify | API test for new `updateAppearance` body |
| `frontend/src/stores/auth.ts` | modify | `tilePreview` computed |
| `frontend/src/views/SettingsView.vue` | modify | second Appearance fieldset + `selectTilePreview` |
| `frontend/src/views/__tests__/SettingsView.spec.ts` | modify | UI test for tile-preview radio; fix existing tone body assertion |
| `frontend/src/views/DocumentListView.vue` | modify | bind thumbnail `<img>` fit class to `auth.tilePreview` |
| `frontend/src/views/__tests__/DocumentListView.spec.ts` | modify | test fit class per mode |
| `docs/api.md`, `docs/frontend.md` | modify | document the new field/setting |
| `journal/260614-tile-preview-mode.md` | create | journal entry |

**Enum values (use these exact strings everywhere):**
- `full_width` → image classes `object-cover object-top` (DEFAULT)
- `whole_page` → image class `object-contain`

---

## Task 1: Backend schema — `TilePreview` enum, default, resolver

**Files:**
- Modify: `src/library/schemas.py` (add after `_resolve_background_tone`, ~line 256; extend `UserPreferences` ~line 280; extend `resolve_preferences` ~line 291)
- Test: `tests/test_settings_api.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_settings_api.py`:

```python
def test_get_settings_includes_default_tile_preview(api_client: TestClient) -> None:
    assert api_client.get("/api/settings").json()["tile_preview"] == "full_width"


def test_get_settings_resolves_unknown_tile_preview_to_default(
    api_client: TestClient, auth_user: AuthUser, api_database_url: str
) -> None:
    _seed_raw_preferences(api_database_url, auth_user.id, {"tile_preview": "sideways"})
    assert api_client.get("/api/settings").json()["tile_preview"] == "full_width"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_settings_api.py::test_get_settings_includes_default_tile_preview tests/test_settings_api.py::test_get_settings_resolves_unknown_tile_preview_to_default -v`
Expected: FAIL — `KeyError: 'tile_preview'` (field not in response).

- [ ] **Step 3: Implement the enum, default, and resolver**

In `src/library/schemas.py`, after `_resolve_background_tone` (line 255), add:

```python
class TilePreview(StrEnum):
    """How a dashboard tile renders the document's first-page thumbnail.

    A4 pages are tall and narrow; the tile box is landscape. ``FULL_WIDTH``
    fills the tile width and crops the lower part of the page (the default);
    ``WHOLE_PAGE`` shows the entire first page letterboxed inside the box.
    The frontend owns the actual CSS object-fit for each value.
    """

    FULL_WIDTH = "full_width"  # fill width, crop bottom — the default
    WHOLE_PAGE = "whole_page"  # show the whole page, letterboxed


DEFAULT_TILE_PREVIEW: Final[TilePreview] = TilePreview.FULL_WIDTH


def _resolve_tile_preview(blob: dict[str, Any]) -> TilePreview:
    """Pick the stored preview mode, falling back for absent/garbage values."""
    raw = blob.get("tile_preview")
    if isinstance(raw, str) and raw in {mode.value for mode in TilePreview}:
        return TilePreview(raw)
    return DEFAULT_TILE_PREVIEW
```

Then add the field to `UserPreferences` (after `background_tone`, line 281):

```python
    dashboard_fields: list[DashboardField]
    background_tone: BackgroundTone
    tile_preview: TilePreview
```

And populate it in `resolve_preferences` (line 291):

```python
    return UserPreferences(
        dashboard_fields=resolve_dashboard_preferences(blob).dashboard_fields,
        background_tone=_resolve_background_tone(blob),
        tile_preview=_resolve_tile_preview(blob),
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_settings_api.py::test_get_settings_includes_default_tile_preview tests/test_settings_api.py::test_get_settings_resolves_unknown_tile_preview_to_default -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/library/schemas.py tests/test_settings_api.py
git commit -m "feat(api): resolve per-user tile_preview from preferences"
```

---

## Task 2: Backend API — persist `tile_preview` via `PUT /settings/appearance`

**Files:**
- Modify: `src/library/schemas.py` — extend `AppearancePreferences` (lines 258-269)
- Modify: `src/library/api/settings.py` — `put_appearance` (lines 51-62)
- Test: `tests/test_settings_api.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_settings_api.py`:

```python
def test_put_appearance_round_trips_tile_preview(api_client: TestClient) -> None:
    put = api_client.put(
        "/api/settings/appearance",
        json={"background_tone": "neutral", "tile_preview": "whole_page"},
    )
    assert put.status_code == 200, put.text
    assert put.json()["tile_preview"] == "whole_page"
    assert api_client.get("/api/settings").json()["tile_preview"] == "whole_page"


def test_put_appearance_unknown_tile_preview_falls_back_to_default(
    api_client: TestClient,
) -> None:
    put = api_client.put(
        "/api/settings/appearance",
        json={"background_tone": "neutral", "tile_preview": "diagonal"},
    )
    assert put.status_code == 200, put.text
    assert put.json()["tile_preview"] == "full_width"


def test_put_appearance_sets_both_tone_and_tile_preview(api_client: TestClient) -> None:
    api_client.put(
        "/api/settings/appearance",
        json={"background_tone": "mist", "tile_preview": "whole_page"},
    )
    body = api_client.get("/api/settings").json()
    assert body["background_tone"] == "mist"
    assert body["tile_preview"] == "whole_page"
```

Note: existing `test_put_appearance_round_trips` and `test_put_appearance_unknown_tone_falls_back_to_default` send only `{"background_tone": ...}`. Because `tile_preview` gets a default via the before-validator (Step 3), those requests still return 200 — leave them unchanged.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_settings_api.py -k tile_preview -v`
Expected: FAIL — `tile_preview` not persisted / 422 if field required without a default validator.

- [ ] **Step 3: Implement the request schema field and persistence**

In `src/library/schemas.py`, extend `AppearancePreferences` (lines 258-269) to:

```python
class AppearancePreferences(BaseModel):
    """Body of PUT /api/settings/appearance — page-canvas tone + tile preview."""

    background_tone: BackgroundTone
    tile_preview: TilePreview = DEFAULT_TILE_PREVIEW

    @field_validator("background_tone", mode="before")
    @classmethod
    def _default_unknown(cls, value: object) -> BackgroundTone:
        """Coerce an unknown/garbage tone to the default (never a 422)."""
        if isinstance(value, str) and value in {tone.value for tone in BackgroundTone}:
            return BackgroundTone(value)
        return DEFAULT_BACKGROUND_TONE

    @field_validator("tile_preview", mode="before")
    @classmethod
    def _default_unknown_tile_preview(cls, value: object) -> TilePreview:
        """Coerce an unknown/garbage preview mode to the default (never a 422)."""
        if isinstance(value, str) and value in {mode.value for mode in TilePreview}:
            return TilePreview(value)
        return DEFAULT_TILE_PREVIEW
```

`tile_preview` has a default so clients that still send only `background_tone` keep working.

In `src/library/api/settings.py`, update `put_appearance` body (lines 56-61):

```python
    """Persist the page-canvas tone + tile preview. Unknown values default."""
    user.preferences = {
        **(user.preferences or {}),
        "background_tone": payload.background_tone.value,
        "tile_preview": payload.tile_preview.value,
    }
    await db.commit()
    return resolve_preferences(user.preferences)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_settings_api.py -v`
Expected: PASS (new tile_preview tests + all existing settings tests).

- [ ] **Step 5: Commit**

```bash
git add src/library/schemas.py src/library/api/settings.py tests/test_settings_api.py
git commit -m "feat(api): persist tile_preview via /settings/appearance"
```

---

## Task 3: Frontend settings API — options, type, default, `updateAppearance`

**Files:**
- Modify: `frontend/src/api/settings.ts`
- Test: `frontend/src/api/__tests__/settings.spec.ts`

- [ ] **Step 1: Write failing test**

Add to `frontend/src/api/__tests__/settings.spec.ts` (import `updateAppearance` and `TILE_PREVIEWS` at top):

```ts
  it('PUT /api/settings/appearance sends both tone and tile preview', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(
        jsonResponse({ dashboard_fields: ['kind'], background_tone: 'slate', tile_preview: 'whole_page' }),
      )
    vi.stubGlobal('fetch', fetchMock)
    const result = await updateAppearance('slate', 'whole_page')
    expect(result.tile_preview).toBe('whole_page')
    const [url, init] = fetchMock.mock.calls[0]!
    expect(String(url)).toBe('/api/settings/appearance')
    expect(init.method).toBe('PUT')
    expect(JSON.parse(init.body)).toEqual({ background_tone: 'slate', tile_preview: 'whole_page' })
  })

  it('TILE_PREVIEWS contains full_width and whole_page in order', () => {
    expect(TILE_PREVIEWS.map((m) => m.value)).toEqual(['full_width', 'whole_page'])
  })
```

Update the existing import line to:
`import { DASHBOARD_FIELDS, TILE_PREVIEWS, getSettings, updateAppearance, updateSettings } from '../settings'`

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/api/__tests__/settings.spec.ts`
Expected: FAIL — `TILE_PREVIEWS` undefined / `updateAppearance` arity mismatch.

- [ ] **Step 3: Implement options, type, default, and updated signature**

In `frontend/src/api/settings.ts`, after the `DEFAULT_BACKGROUND_TONE` export (line 42), add:

```ts
/**
 * How a dashboard tile renders the document's first-page thumbnail
 * (Settings → Appearance). `full_width` fills the tile width and crops the
 * lower part of the page; `whole_page` shows the entire first page letterboxed.
 * `full_width` is the default (mirrors the backend's DEFAULT_TILE_PREVIEW).
 */
export const TILE_PREVIEWS = [
  { value: 'full_width', text: 'Full width', hint: 'Fills the tile; crops the lower part of the page.' },
  { value: 'whole_page', text: 'Whole page', hint: 'Shows the entire first page, letterboxed.' },
] as const

export type TilePreview = (typeof TILE_PREVIEWS)[number]['value']

export const DEFAULT_TILE_PREVIEW: TilePreview = 'full_width'
```

Add `tile_preview` to `UserPreferences` (lines 44-49):

```ts
export interface UserPreferences {
  dashboard_fields: DashboardField[]
  // Optional on the client so older payloads (and test fixtures) without the
  // key still type-check; consumers fall back to the defaults.
  background_tone?: BackgroundTone
  tile_preview?: TilePreview
}
```

Replace `updateAppearance` (lines 61-67) with:

```ts
/** PUT /api/settings/appearance — persist the page-canvas tone and tile preview. */
export function updateAppearance(
  tone: BackgroundTone,
  tilePreview: TilePreview,
): Promise<UserPreferences> {
  return apiFetch<UserPreferences>('/api/settings/appearance', {
    method: 'PUT',
    body: { background_tone: tone, tile_preview: tilePreview },
  })
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run src/api/__tests__/settings.spec.ts`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/api/settings.ts frontend/src/api/__tests__/settings.spec.ts
git commit -m "feat(frontend): tile_preview options and updateAppearance arg"
```

---

## Task 4: Auth store — `tilePreview` computed

**Files:**
- Modify: `frontend/src/stores/auth.ts`

- [ ] **Step 1: Implement the computed (no separate test; covered via SettingsView/DocumentListView tests in Tasks 5-6)**

In `frontend/src/stores/auth.ts`, extend the settings import (lines 4-9):

```ts
import {
  DEFAULT_BACKGROUND_TONE,
  DEFAULT_TILE_PREVIEW,
  type BackgroundTone,
  type DashboardField,
  type TilePreview,
  type UserPreferences,
} from '@/api/settings'
```

After the `backgroundTone` computed (line 30), add:

```ts
  // How dashboard tiles render the first-page thumbnail. Defaults when the
  // user is absent or a payload predates the preference.
  const tilePreview = computed<TilePreview>(
    () => user.value?.preferences?.tile_preview ?? DEFAULT_TILE_PREVIEW,
  )
```

Add `tilePreview` to the store's return object (after `backgroundTone`, line 79):

```ts
    backgroundTone,
    tilePreview,
```

- [ ] **Step 2: Type-check**

Run: `cd frontend && npx vue-tsc --noEmit -p tsconfig.app.json`
Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/stores/auth.ts
git commit -m "feat(frontend): expose tilePreview from auth store"
```

---

## Task 5: Settings UI — Appearance "Document previews" fieldset

**Files:**
- Modify: `frontend/src/views/SettingsView.vue`
- Test: `frontend/src/views/__tests__/SettingsView.spec.ts`

- [ ] **Step 1: Write failing tests + fix existing tone-body assertion**

In `frontend/src/views/__tests__/SettingsView.spec.ts`:

First, fix the existing test `selecting a background tone in the Appearance tab saves and applies it`. Its body assertion (line 88) must now include `tile_preview` (the store seeds `full_width` by default):

```ts
    expect(JSON.parse(init.body)).toEqual({ background_tone: 'slate', tile_preview: 'full_width' })
```

Then add a new test:

```ts
  it('selecting a tile preview in the Appearance tab saves and applies it', async () => {
    const auth = useAuthStore()
    auth.user = {
      id: 1,
      username: 'a',
      display_name: 'A',
      preferences: { dashboard_fields: ['kind'], background_tone: 'neutral', tile_preview: 'full_width' },
    }
    fetchMock.mockResolvedValue(
      jsonResponse({ dashboard_fields: ['kind'], background_tone: 'neutral', tile_preview: 'whole_page' }),
    )

    const wrapper = mount(SettingsView, { global: { stubs: { RouterLink: true } } })
    await wrapper.find('[data-testid="tab-appearance-btn"]').trigger('click')
    expect(wrapper.find('[data-testid="tile-full_width"]').attributes('aria-checked')).toBe('true')

    await wrapper.find('[data-testid="tile-whole_page"]').trigger('click')
    await flushPromises()

    const [url, init] = fetchMock.mock.calls.at(-1)!
    expect(String(url)).toBe('/api/settings/appearance')
    expect(JSON.parse(init.body)).toEqual({ background_tone: 'neutral', tile_preview: 'whole_page' })
    expect(wrapper.find('[data-testid="tile-whole_page"]').attributes('aria-checked')).toBe('true')
    expect(auth.tilePreview).toBe('whole_page')
  })
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd frontend && npx vitest run src/views/__tests__/SettingsView.spec.ts`
Expected: FAIL — `tile-full_width` not found; existing tone test fails on the body shape.

- [ ] **Step 3: Implement the fieldset and handler**

In `frontend/src/views/SettingsView.vue`:

Extend the settings import (lines 12-20) to add `TILE_PREVIEWS`, `DEFAULT_TILE_PREVIEW`, and `type TilePreview`:

```ts
import {
  BACKGROUND_TONES,
  DASHBOARD_FIELDS,
  DEFAULT_BACKGROUND_TONE,
  DEFAULT_TILE_PREVIEW,
  TILE_PREVIEWS,
  updateAppearance,
  updateSettings,
  type BackgroundTone,
  type DashboardField,
  type TilePreview,
} from '@/api/settings'
```

Update `selectTone` to pass the current tile preview (line 71). Replace `const result = await updateAppearance(tone)` with:

```ts
    const result = await updateAppearance(tone, selectedTilePreview.value)
```

After `selectTone` (line 79), add the tile-preview state + handler:

```ts
const selectedTilePreview = ref<TilePreview>(auth.tilePreview)

async function selectTilePreview(mode: TilePreview): Promise<void> {
  if (mode === selectedTilePreview.value) return
  const previous = selectedTilePreview.value
  selectedTilePreview.value = mode
  toneError.value = null
  // Optimistic: update the store now so the dashboard reflects it immediately.
  if (auth.user) auth.applyPreferences({ ...auth.user.preferences, tile_preview: mode })
  try {
    const result = await updateAppearance(selectedTone.value, mode)
    auth.applyPreferences(result)
    selectedTilePreview.value = result.tile_preview ?? DEFAULT_TILE_PREVIEW
  } catch {
    selectedTilePreview.value = previous
    if (auth.user) auth.applyPreferences({ ...auth.user.preferences, tile_preview: previous })
    toneError.value = 'Sorry, your appearance preference could not be saved. Try again.'
  }
}
```

In the template, inside the Appearance `<section>`, add a second card after the "Page background" card's closing `</div>` (after line 197), before the section closes:

```html
      <div :class="cardClass" class="mt-6">
        <fieldset>
          <legend class="text-lg font-semibold text-gray-800 dark:text-gray-100">
            Document previews
          </legend>
          <p class="text-sm text-gray-500 dark:text-gray-400 mt-1">
            How each dashboard tile shows the document's first page. Saves to your account
            automatically.
          </p>
          <div role="radiogroup" aria-label="Document previews" class="grid grid-cols-1 sm:grid-cols-2 gap-3 mt-5">
            <button
              v-for="mode in TILE_PREVIEWS"
              :key="mode.value"
              type="button"
              role="radio"
              :aria-checked="selectedTilePreview === mode.value"
              :data-testid="`tile-${mode.value}`"
              :class="[
                'flex flex-col gap-1 rounded-lg border p-3 text-left transition cursor-pointer',
                selectedTilePreview === mode.value
                  ? 'border-violet-500 ring-2 ring-violet-500/30'
                  : 'border-gray-200 dark:border-gray-700/60 hover:border-gray-300 dark:hover:border-gray-600',
              ]"
              @click="selectTilePreview(mode.value)"
            >
              <span class="text-sm font-medium text-gray-700 dark:text-gray-200">{{ mode.text }}</span>
              <span class="text-xs text-gray-500 dark:text-gray-400">{{ mode.hint }}</span>
            </button>
          </div>
        </fieldset>
      </div>
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd frontend && npx vitest run src/views/__tests__/SettingsView.spec.ts`
Expected: PASS (new tile-preview test + the fixed tone test + the rest).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/views/SettingsView.vue frontend/src/views/__tests__/SettingsView.spec.ts
git commit -m "feat(frontend): Document previews setting in Appearance tab"
```

---

## Task 6: Consume `tilePreview` on the dashboard thumbnail

**Files:**
- Modify: `frontend/src/views/DocumentListView.vue` (script ~line 36; template lines 291-298)
- Test: `frontend/src/views/__tests__/DocumentListView.spec.ts`

- [ ] **Step 1: Write failing test**

Open `frontend/src/views/__tests__/DocumentListView.spec.ts` to match its existing mount/fetch-stub pattern (it already mounts the view with at least one `has_thumbnail` item). Add a test asserting the thumbnail image fit class follows the store's `tilePreview`. Use the existing helper(s) in that file to mount; the key assertions:

```ts
  it('uses object-cover top crop for full_width tile preview', async () => {
    // ... mount with auth.user.preferences.tile_preview = 'full_width' and an
    // item where has_thumbnail === true, following this file's existing setup ...
    const img = wrapper.find('[data-testid="doc-card"] img')
    expect(img.classes()).toContain('object-cover')
    expect(img.classes()).toContain('object-top')
  })

  it('uses object-contain for whole_page tile preview', async () => {
    // ... same, but tile_preview = 'whole_page' ...
    const img = wrapper.find('[data-testid="doc-card"] img')
    expect(img.classes()).toContain('object-contain')
  })
```

If the file has no existing mount helper, copy the mount + fetch-stub from the first test in the file and set `auth.user` (via `useAuthStore()`) with `preferences.tile_preview` before mounting, mirroring `SettingsView.spec.ts`'s store seeding.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/views/__tests__/DocumentListView.spec.ts`
Expected: FAIL — image still has the hard-coded `object-contain` for both modes.

- [ ] **Step 3: Implement the reactive fit class**

In `frontend/src/views/DocumentListView.vue` script (after `const auth = useAuthStore()`, line 36), add:

```ts
const thumbnailFitClass = computed<string>(() =>
  auth.tilePreview === 'whole_page' ? 'object-contain' : 'object-cover object-top',
)
```

(`computed` is already imported on line 13.)

Update the thumbnail `<img>` class (line 293) from:

```html
            class="aspect-[4/3] w-full object-contain"
```

to:

```html
            :class="['aspect-[4/3] w-full', thumbnailFitClass]"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run src/views/__tests__/DocumentListView.spec.ts`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/views/DocumentListView.vue frontend/src/views/__tests__/DocumentListView.spec.ts
git commit -m "feat(frontend): apply tile preview mode to dashboard thumbnails"
```

---

## Task 7: Full suites, docs, and journal

**Files:**
- Modify: `docs/api.md`, `docs/frontend.md`
- Create: `journal/260614-tile-preview-mode.md`

- [ ] **Step 1: Run the full backend and frontend suites**

Run: `uv run pytest tests/test_settings_api.py -v`
Expected: PASS.
Run: `cd frontend && npx vitest run && npx vue-tsc --noEmit -p tsconfig.app.json`
Expected: all tests PASS, no type errors.

- [ ] **Step 2: Update `docs/api.md`**

In the `/api/settings` section, document the new `tile_preview` field on `UserPreferences` (values `full_width` default, `whole_page`) and that `PUT /api/settings/appearance` now accepts `tile_preview` alongside `background_tone`, both defaulting on unknown values. Keep the wording consistent with the existing `background_tone` description.

- [ ] **Step 3: Update `docs/frontend.md`**

In the Settings/Appearance description, note the new "Document previews" choice: full-width top crop (default) vs whole-page letterbox, consumed on the dashboard tile thumbnail via `auth.tilePreview`.

- [ ] **Step 4: Create the journal entry**

Create `journal/260614-tile-preview-mode.md` capturing: the problem (A4 letterbox wasted space), the decision (per-user `tile_preview` enum reusing the appearance endpoint, full_width default, box height unchanged), and the touched layers.

- [ ] **Step 5: Commit**

```bash
git add docs/api.md docs/frontend.md journal/260614-tile-preview-mode.md
git commit -m "docs: document tile preview mode setting"
```

---

## Self-review notes

- **Spec coverage:** enum values + default (Task 1/3), backend persist (Task 2), frontend API (Task 3), store (Task 4), Settings UI with optimistic+rollback (Task 5), tile consumption with unchanged aspect box (Task 6), tests at every layer, docs/journal (Task 7). All spec sections covered.
- **Breaking-change watch:** `updateAppearance` gains a required 2nd arg — every caller (`selectTone`, `selectTilePreview`) updated in Task 5; the existing `settings.spec.ts` and `SettingsView.spec.ts` body assertions are updated in Tasks 3 and 5 respectively.
- **Type consistency:** `TilePreview` / `tile_preview` / `tilePreview` used consistently; values `full_width` and `whole_page` fixed across backend enum, frontend options, and image classes (`object-cover object-top` / `object-contain`).

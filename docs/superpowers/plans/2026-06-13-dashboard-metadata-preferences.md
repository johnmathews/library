# Dashboard Metadata Preferences Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let each user choose, on a new in-app Settings page, which metadata fields appear on the dashboard document tiles; persist the choice per-user server-side.

**Architecture:** A `preferences` JSONB column on `users` stores `{"dashboard_fields": [...]}`. A validated Pydantic `DashboardPreferences` schema cleans the field list (drops unknowns, dedupes) and fills a default when absent. Preferences are read via `GET /api/settings` and embedded in `GET /api/auth/me`; written via `PUT /api/settings`. The Vue dashboard reads the field set from the auth store and renders each tile field conditionally; a `SettingsView` with GOV.UK checkboxes edits it.

**Tech Stack:** FastAPI, SQLAlchemy 2.0 (async) + Alembic, Pydantic v2, PostgreSQL JSONB; Vue 3 + Pinia + vue-router + TypeScript; pytest, Vitest, Playwright.

**Spec:** `docs/superpowers/specs/2026-06-13-dashboard-metadata-preferences-design.md`

**Canonical field keys** (single source of truth across backend & frontend):
`kind`, `sender`, `tags`, `date`, `language`, `status`, `amount`, `file_type`.
**Default set** (absent prefs): `kind, sender, tags, date, language, status`.

---

## Task 1: `preferences` column on the User model + migration

**Files:**
- Modify: `src/library/models.py:97-105` (User model)
- Create: `migrations/versions/0003_user_preferences.py`
- Test: `tests/test_migrations.py` (already runs upgrade→head; add a column assertion)

- [ ] **Step 1: Write the failing test**

Add to `tests/test_migrations.py` (it already has the migrated DB; follow the existing style — inspect `information_schema`). Append:

```python
def test_users_have_preferences_column(migrated_database_url: str) -> None:
    rows = fetch_all(
        migrated_database_url,
        """
        SELECT column_name, data_type, is_nullable
        FROM information_schema.columns
        WHERE table_name = 'users' AND column_name = 'preferences'
        """,
    )
    assert rows == [("preferences", "jsonb", "NO")]
```

Ensure `from tests.conftest import fetch_all` (or the module-local import already used) is present.

- [ ] **Step 2: Run it — expect FAIL**

Run: `uv run pytest tests/test_migrations.py::test_users_have_preferences_column -v`
Expected: FAIL — no `preferences` column (empty result list `[]`).

- [ ] **Step 3: Add the column to the model**

In `src/library/models.py`, inside `class User`, after the `created_at` line (`:105`), add:

```python
    preferences: Mapped[dict[str, Any]] = mapped_column(
        JSONB, server_default=text("'{}'::jsonb"), default=dict
    )
```

`JSONB`, `text`, and `Any` are already imported (`models.py:42`, `:21-42`, `:19`).

- [ ] **Step 4: Write the migration**

Create `migrations/versions/0003_user_preferences.py`:

```python
"""user preferences

Revision ID: 0003
Revises: 0002
Create Date: 2026-06-13 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "preferences",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "preferences")
```

- [ ] **Step 5: Run the migration test — expect PASS**

Run: `uv run pytest tests/test_migrations.py -v`
Expected: PASS (the session-scoped migrated DB is rebuilt and the new column asserts).

- [ ] **Step 6: Commit**

```bash
git add src/library/models.py migrations/versions/0003_user_preferences.py tests/test_migrations.py
git commit -m "feat(db): add preferences JSONB column to users"
```

---

## Task 2: `DashboardPreferences` schema, resolver, and `/auth/me` exposure

**Files:**
- Modify: `src/library/schemas.py` (add enum + schema near `UserOut`, `:157`)
- Modify: `src/library/api/auth.py:38-39` (`_user_out`) and `:26-32` (imports)
- Test: `tests/test_auth.py`

- [ ] **Step 1: Write the failing schema tests**

Create `tests/test_preferences_schema.py`:

```python
from library.schemas import (
    DEFAULT_DASHBOARD_FIELDS,
    DashboardField,
    DashboardPreferences,
    resolve_dashboard_preferences,
)


def test_unknown_keys_dropped_and_deduped() -> None:
    prefs = DashboardPreferences(
        dashboard_fields=["kind", "bogus", "kind", "tags"]
    )
    assert prefs.dashboard_fields == [DashboardField.KIND, DashboardField.TAGS]


def test_non_list_coerces_to_empty() -> None:
    prefs = DashboardPreferences(dashboard_fields="kind")  # type: ignore[arg-type]
    assert prefs.dashboard_fields == []


def test_resolve_absent_key_returns_default() -> None:
    assert resolve_dashboard_preferences({}).dashboard_fields == DEFAULT_DASHBOARD_FIELDS
    assert resolve_dashboard_preferences(None).dashboard_fields == DEFAULT_DASHBOARD_FIELDS


def test_resolve_explicit_empty_stays_empty() -> None:
    # A user who turned everything off keeps an empty list (not the default).
    assert resolve_dashboard_preferences({"dashboard_fields": []}).dashboard_fields == []


def test_resolve_cleans_stored_garbage() -> None:
    resolved = resolve_dashboard_preferences({"dashboard_fields": ["tags", "nope", 7]})
    assert resolved.dashboard_fields == [DashboardField.TAGS]
```

- [ ] **Step 2: Run — expect FAIL (import error)**

Run: `uv run pytest tests/test_preferences_schema.py -v`
Expected: FAIL — `ImportError` (symbols don't exist yet).

- [ ] **Step 3: Implement the schema**

In `src/library/schemas.py`: add `field_validator` to the pydantic import (`:11`) and `from enum import StrEnum` to the top imports (after `from datetime import ...`). Then add, just above `class UserOut` (`:157`):

```python
class DashboardField(StrEnum):
    """A metadata field that can be shown on a dashboard tile."""

    KIND = "kind"
    SENDER = "sender"
    TAGS = "tags"
    DATE = "date"
    LANGUAGE = "language"
    STATUS = "status"
    AMOUNT = "amount"
    FILE_TYPE = "file_type"


DEFAULT_DASHBOARD_FIELDS: list[DashboardField] = [
    DashboardField.KIND,
    DashboardField.SENDER,
    DashboardField.TAGS,
    DashboardField.DATE,
    DashboardField.LANGUAGE,
    DashboardField.STATUS,
]


class DashboardPreferences(BaseModel):
    """Which metadata fields appear on the dashboard tiles, in render order."""

    dashboard_fields: list[DashboardField]

    @field_validator("dashboard_fields", mode="before")
    @classmethod
    def _clean(cls, value: object) -> list[str]:
        """Keep only known field keys, de-duplicated, order preserved.

        Tolerant on purpose: unknown/garbage values are dropped (never a
        422 or 500), so a hand-edited row or a renamed field can't break
        the dashboard.
        """
        if not isinstance(value, list):
            return []
        valid = {field.value for field in DashboardField}
        seen: set[str] = set()
        cleaned: list[str] = []
        for item in value:
            if isinstance(item, str) and item in valid and item not in seen:
                seen.add(item)
                cleaned.append(item)
        return cleaned


def resolve_dashboard_preferences(
    preferences: dict[str, Any] | None,
) -> DashboardPreferences:
    """Resolve a user's stored ``preferences`` blob to display fields.

    Absent ``dashboard_fields`` key → the default set. An explicit (even
    empty) list is honoured and cleaned.
    """
    blob = preferences or {}
    if "dashboard_fields" not in blob:
        return DashboardPreferences(dashboard_fields=DEFAULT_DASHBOARD_FIELDS)
    return DashboardPreferences(dashboard_fields=blob["dashboard_fields"])
```

Then extend `UserOut` (`:157`) with the preferences field:

```python
class UserOut(BaseModel):
    """The authenticated user (login response and GET /api/auth/me)."""

    id: int
    username: str
    display_name: str
    preferences: DashboardPreferences
```

- [ ] **Step 4: Populate it in `_user_out`**

In `src/library/api/auth.py`, add `DashboardPreferences`-related imports to the `from library.schemas import (...)` block (`:26-32`): add `resolve_dashboard_preferences`. Then change `_user_out` (`:38-39`):

```python
def _user_out(user: User) -> UserOut:
    return UserOut(
        id=user.id,
        username=user.username,
        display_name=user.display_name,
        preferences=resolve_dashboard_preferences(user.preferences),
    )
```

- [ ] **Step 5: Add an `/auth/me` assertion**

In `tests/test_auth.py`, find an existing test that calls `GET /api/auth/me` (search `auth/me`) and add a sibling test (use the `api_client` fixture):

```python
def test_me_includes_default_preferences(api_client: TestClient) -> None:
    body = api_client.get("/api/auth/me").json()
    assert body["preferences"]["dashboard_fields"] == [
        "kind", "sender", "tags", "date", "language", "status",
    ]
```

- [ ] **Step 6: Fix any exact-match body assertions**

`UserOut` now has a `preferences` key, so the `/auth/me` and `/auth/login`
responses carry one extra field. Search `tests/test_auth.py` for assertions
that compare the whole response body with `==` (e.g. `assert resp.json() == {...}`)
and either add the `preferences` key to the expected dict or assert per-key
(`body["username"] == ...`). Leave field-by-field assertions untouched.

- [ ] **Step 7: Run — expect PASS**

Run: `uv run pytest tests/test_preferences_schema.py tests/test_auth.py -v`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add src/library/schemas.py src/library/api/auth.py tests/test_preferences_schema.py tests/test_auth.py
git commit -m "feat(api): DashboardPreferences schema + /auth/me exposure"
```

---

## Task 3: `GET`/`PUT /api/settings` endpoints

**Files:**
- Create: `src/library/api/settings.py`
- Modify: `src/library/app.py:12` (import) and `:132-135` (include router)
- Test: `tests/test_settings_api.py`

- [ ] **Step 1: Write the failing endpoint tests**

Create `tests/test_settings_api.py`:

```python
from fastapi.testclient import TestClient


def test_get_settings_defaults(api_client: TestClient) -> None:
    body = api_client.get("/api/settings").json()
    assert body["dashboard_fields"] == [
        "kind", "sender", "tags", "date", "language", "status",
    ]


def test_put_settings_round_trips(api_client: TestClient) -> None:
    put = api_client.put("/api/settings", json={"dashboard_fields": ["amount", "tags"]})
    assert put.status_code == 200, put.text
    assert put.json()["dashboard_fields"] == ["amount", "tags"]
    # Persisted: a fresh GET reflects it, and so does /auth/me.
    assert api_client.get("/api/settings").json()["dashboard_fields"] == ["amount", "tags"]
    assert api_client.get("/api/auth/me").json()["preferences"]["dashboard_fields"] == [
        "amount", "tags",
    ]


def test_put_settings_drops_unknown_and_dedupes(api_client: TestClient) -> None:
    put = api_client.put(
        "/api/settings", json={"dashboard_fields": ["kind", "kind", "nope"]}
    )
    assert put.status_code == 200, put.text
    assert put.json()["dashboard_fields"] == ["kind"]


def test_put_settings_empty_list_shows_nothing(api_client: TestClient) -> None:
    put = api_client.put("/api/settings", json={"dashboard_fields": []})
    assert put.status_code == 200, put.text
    assert api_client.get("/api/settings").json()["dashboard_fields"] == []


def test_settings_requires_auth(anon_client: TestClient) -> None:
    assert anon_client.get("/api/settings").status_code == 401
```

- [ ] **Step 2: Run — expect FAIL (404)**

Run: `uv run pytest tests/test_settings_api.py -v`
Expected: FAIL — 404 (router not mounted).

- [ ] **Step 3: Create the settings router**

Create `src/library/api/settings.py`:

```python
"""User settings: per-user display preferences (docs/api.md)."""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from library.auth.deps import current_user
from library.db import get_session
from library.models import User
from library.schemas import DashboardPreferences, resolve_dashboard_preferences

router: APIRouter = APIRouter(tags=["settings"])


@router.get("/settings", response_model=DashboardPreferences, summary="Your display preferences")
async def get_settings(
    user: Annotated[User, Depends(current_user)],
) -> DashboardPreferences:
    """Resolved dashboard field preferences (defaults filled when unset)."""
    return resolve_dashboard_preferences(user.preferences)


@router.put("/settings", response_model=DashboardPreferences, summary="Update your preferences")
async def put_settings(
    payload: DashboardPreferences,
    db: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(current_user)],
) -> DashboardPreferences:
    """Persist the dashboard field list. Unknown keys are dropped (200)."""
    # Reassign the whole dict so SQLAlchemy detects the JSONB change.
    user.preferences = {
        **(user.preferences or {}),
        "dashboard_fields": [field.value for field in payload.dashboard_fields],
    }
    await db.commit()
    return payload
```

- [ ] **Step 4: Mount the router**

In `src/library/app.py`: add `settings` to the import (`:12`):

```python
from library.api import auth, documents, jobs, settings, taxonomy
```

And include it in the protected `api_router` block (after `:135`, alongside the others):

```python
    api_router.include_router(settings.router)
```

- [ ] **Step 5: Add the OpenAPI tag (optional but matches convention)**

In `src/library/app.py` `OPENAPI_TAGS` (`:33-60`), append:

```python
    {"name": "settings", "description": "Per-user display preferences."},
```

- [ ] **Step 6: Run — expect PASS**

Run: `uv run pytest tests/test_settings_api.py -v`
Expected: PASS (all 5).

- [ ] **Step 7: Commit**

```bash
git add src/library/api/settings.py src/library/app.py tests/test_settings_api.py
git commit -m "feat(api): GET/PUT /api/settings for dashboard preferences"
```

---

## Task 4: Add `amount_total`/`currency` to the document list item

**Files:**
- Modify: `src/library/schemas.py:54-81` (`DocumentListItem`) and `:93-99` (`DocumentDetail`)
- Modify: `src/library/api/documents.py:459-483` (`_list_item_fields`) and `:486-495` (`_detail`)
- Test: `tests/test_documents_api.py`

- [ ] **Step 1: Write the failing test**

In `tests/test_documents_api.py`, add a test asserting a listed document carries `amount_total`/`currency`. Find an existing test that creates a document with an amount (search `amount_total`) and model this after it; if none, add:

```python
def test_list_item_includes_amount(api_client: TestClient) -> None:
    # Create a doc, set an amount via PATCH, then list it.
    doc_id = _create_indexed_document(api_client)  # reuse the file's existing helper
    api_client.patch(f"/api/documents/{doc_id}", json={"amount_total": "92.50", "currency": "EUR"})
    item = next(
        d for d in api_client.get("/api/documents").json()["items"] if d["id"] == doc_id
    )
    assert item["amount_total"] == "92.50"
    assert item["currency"] == "EUR"
```

If `_create_indexed_document` (or equivalent) does not exist in the file, use the pattern the file already uses to insert a document (check the top of `tests/test_documents_api.py` for its document-creation helper and call that).

- [ ] **Step 2: Run — expect FAIL (KeyError/None)**

Run: `uv run pytest tests/test_documents_api.py::test_list_item_includes_amount -v`
Expected: FAIL — `amount_total` not present on the list item.

- [ ] **Step 3: Move the fields into the list schema**

In `src/library/schemas.py`, add to `DocumentListItem` (after `has_thumbnail`, `:70`):

```python
    amount_total: Decimal | None = None
    currency: str | None = None
```

Then in `DocumentDetail` (`:93-99`), **remove** the now-inherited `amount_total` and `currency` lines (they move up; leaving them is a harmless re-declaration but keep it DRY — delete them from `DocumentDetail`).

- [ ] **Step 4: Populate them in `_list_item_fields`**

In `src/library/api/documents.py`, add to the dict returned by `_list_item_fields` (`:482`, after `has_thumbnail`):

```python
        "amount_total": document.amount_total,
        "currency": document.currency,
```

Then in `_detail` (`:486-495`), **remove** the explicit `amount_total=document.amount_total,` and `currency=document.currency,` kwargs — they now come from `**_list_item_fields(document)`. (Leaving them causes a `TypeError: got multiple values`.)

- [ ] **Step 5: Run — expect PASS (and no regression in detail)**

Run: `uv run pytest tests/test_documents_api.py -v`
Expected: PASS (the new test and all existing detail tests).

- [ ] **Step 6: Commit**

```bash
git add src/library/schemas.py src/library/api/documents.py tests/test_documents_api.py
git commit -m "feat(api): expose amount_total/currency on the document list item"
```

---

## Task 5: Frontend settings API client + list-item types

**Files:**
- Create: `frontend/src/api/settings.ts`
- Modify: `frontend/src/api/documents.ts:37-59` (`DocumentListItem`), `:70-83` (`DocumentDetail`)
- Test: `frontend/src/api/__tests__/settings.spec.ts` (create)

- [ ] **Step 1: Write the failing client test**

Create `frontend/src/api/__tests__/settings.spec.ts`:

```ts
import { afterEach, describe, expect, it, vi } from 'vitest'
import { getSettings, updateSettings } from '../settings'

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

describe('settings api', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('GET /api/settings returns the field list', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse({ dashboard_fields: ['kind'] })))
    expect(await getSettings()).toEqual({ dashboard_fields: ['kind'] })
  })

  it('PUT /api/settings sends the field list', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ dashboard_fields: ['tags'] }))
    vi.stubGlobal('fetch', fetchMock)
    const result = await updateSettings({ dashboard_fields: ['tags'] })
    expect(result).toEqual({ dashboard_fields: ['tags'] })
    const [, init] = fetchMock.mock.calls[0]
    expect(init.method).toBe('PUT')
    expect(JSON.parse(init.body)).toEqual({ dashboard_fields: ['tags'] })
  })
})
```

- [ ] **Step 2: Run — expect FAIL (module missing)**

Run: `cd frontend && npx vitest run src/api/__tests__/settings.spec.ts`
Expected: FAIL — cannot resolve `../settings`.

- [ ] **Step 3: Implement the client**

Create `frontend/src/api/settings.ts`:

```ts
/** Per-user display preferences (docs/api.md — /api/settings). */
import { apiFetch } from './client'

/**
 * The selectable dashboard tile fields, in canonical render order. This
 * array is the single frontend source of truth for both the settings
 * checkboxes and the order fields appear on a tile.
 */
export const DASHBOARD_FIELDS = [
  { value: 'kind', text: 'Document type' },
  { value: 'sender', text: 'Correspondent' },
  { value: 'tags', text: 'Tags' },
  { value: 'date', text: 'Date' },
  { value: 'language', text: 'Language' },
  { value: 'status', text: 'Status' },
  { value: 'amount', text: 'Amount' },
  { value: 'file_type', text: 'File type' },
] as const

export type DashboardField = (typeof DASHBOARD_FIELDS)[number]['value']

export interface DashboardPreferences {
  dashboard_fields: DashboardField[]
}

/** GET /api/settings — resolved dashboard field preferences. */
export function getSettings(): Promise<DashboardPreferences> {
  return apiFetch<DashboardPreferences>('/api/settings')
}

/** PUT /api/settings — persist the field list; returns the cleaned set. */
export function updateSettings(prefs: DashboardPreferences): Promise<DashboardPreferences> {
  return apiFetch<DashboardPreferences>('/api/settings', { method: 'PUT', body: prefs })
}
```

- [ ] **Step 4: Add the new fields to the document types**

In `frontend/src/api/documents.ts`, add to `DocumentListItem` (after `has_thumbnail`, `:51`):

```ts
  amount_total: string | null
  currency: string | null
```

Then in `DocumentDetail` (`:70-83`), **remove** the now-inherited `amount_total: string | null` and `currency: string | null` lines (kept identical in the base interface).

- [ ] **Step 5: Run — expect PASS**

Run: `cd frontend && npx vitest run src/api/__tests__/settings.spec.ts`
Expected: PASS. (Type errors surface in Task 8's type-check; `makeItem` helper updated there.)

- [ ] **Step 6: Commit**

```bash
git add frontend/src/api/settings.ts frontend/src/api/documents.ts frontend/src/api/__tests__/settings.spec.ts
git commit -m "feat(web): settings API client + amount/currency list-item types"
```

---

## Task 6: Auth store carries preferences

**Files:**
- Modify: `frontend/src/stores/auth.ts`
- Test: `frontend/src/stores/__tests__/auth.spec.ts`

- [ ] **Step 1: Write the failing store test**

In `frontend/src/stores/__tests__/auth.spec.ts`, add (match the file's existing fetch-stub style; the `User` mock must now include `preferences`):

```ts
it('exposes dashboardFields from the loaded user', async () => {
  const store = useAuthStore()
  // assume the file's helper stubs /api/auth/me; ensure its body includes:
  // preferences: { dashboard_fields: ['kind', 'tags'] }
  await store.ensureLoaded()
  expect(store.dashboardFields).toEqual(['kind', 'tags'])
})

it('applyPreferences updates the field set', () => {
  const store = useAuthStore()
  store.applyPreferences({ dashboard_fields: ['amount'] })
  expect(store.dashboardFields).toEqual(['amount'])
})
```

Update any existing `/api/auth/me` mock bodies in this file to include `preferences: { dashboard_fields: [...] }` (the `User` type now requires it).

- [ ] **Step 2: Run — expect FAIL**

Run: `cd frontend && npx vitest run src/stores/__tests__/auth.spec.ts`
Expected: FAIL — `dashboardFields`/`applyPreferences` undefined.

- [ ] **Step 3: Extend the store**

In `frontend/src/stores/auth.ts`:
- Import the prefs type at the top: `import type { DashboardField, DashboardPreferences } from '@/api/settings'`
- Add `preferences` to the `User` interface (`:5-9`):

```ts
export interface User {
  id: number
  username: string
  display_name: string
  preferences: DashboardPreferences
}
```

- Inside the store, after `isAuthenticated` (`:13`), add:

```ts
  const dashboardFields = computed<DashboardField[]>(
    () => user.value?.preferences?.dashboard_fields ?? [],
  )

  function applyPreferences(preferences: DashboardPreferences): void {
    if (user.value) user.value.preferences = preferences
  }
```

- Add both to the returned object (`:54`): `dashboardFields, applyPreferences`.

- [ ] **Step 3b: Update existing User mocks for the new required field**

`User` now requires `preferences`. Grep the frontend tests for places that
build a `User` or stub `/api/auth/me` (at least `src/router/__tests__/guard.spec.ts`,
`src/__tests__/App.spec.ts`, and any in this store's own spec) and add
`preferences: { dashboard_fields: [] }` to each. `npm run type-check` (Task 8)
will flag any missed; fix those too.

- [ ] **Step 4: Run — expect PASS**

Run: `cd frontend && npx vitest run src/stores/__tests__/auth.spec.ts`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/stores/auth.ts frontend/src/stores/__tests__/auth.spec.ts
git commit -m "feat(web): auth store carries dashboard field preferences"
```

---

## Task 7: Settings page (view, route, nav link)

**Files:**
- Create: `frontend/src/views/SettingsView.vue`
- Modify: `frontend/src/router/index.ts:10-39` (route), `frontend/src/App.vue:21-35` (nav)
- Test: `frontend/src/views/__tests__/SettingsView.spec.ts` (create)

- [ ] **Step 1: Write the failing view test**

Create `frontend/src/views/__tests__/SettingsView.spec.ts`:

```ts
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import SettingsView from '../SettingsView.vue'
import { useAuthStore } from '@/stores/auth'

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

describe('SettingsView', () => {
  const fetchMock = vi.fn()
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.stubGlobal('fetch', fetchMock)
    fetchMock.mockReset()
  })

  it('saves the selected fields and shows a confirmation', async () => {
    const auth = useAuthStore()
    auth.user = { id: 1, username: 'a', display_name: 'A', preferences: { dashboard_fields: ['kind'] } }
    fetchMock.mockResolvedValue(jsonResponse({ dashboard_fields: ['kind', 'tags'] }))

    const wrapper = mount(SettingsView, { global: { stubs: { RouterLink: true } } })
    // tick the 'tags' checkbox on
    await wrapper.find('input[value="tags"]').setValue(true)
    await wrapper.find('form').trigger('submit.prevent')
    await flushPromises()

    const [url, init] = fetchMock.mock.calls.at(-1)!
    expect(String(url)).toBe('/api/settings')
    expect(init.method).toBe('PUT')
    expect(wrapper.find('[data-testid="settings-saved"]').exists()).toBe(true)
    expect(auth.dashboardFields).toEqual(['kind', 'tags'])
  })

  it('shows an error and leaves prefs unchanged on save failure', async () => {
    const auth = useAuthStore()
    auth.user = { id: 1, username: 'a', display_name: 'A', preferences: { dashboard_fields: ['kind'] } }
    fetchMock.mockResolvedValue(jsonResponse({ detail: 'boom' }, 500))

    const wrapper = mount(SettingsView, { global: { stubs: { RouterLink: true } } })
    await wrapper.find('form').trigger('submit.prevent')
    await flushPromises()

    expect(wrapper.find('[data-testid="settings-error"]').exists()).toBe(true)
    expect(auth.dashboardFields).toEqual(['kind'])
  })
})
```

- [ ] **Step 2: Run — expect FAIL (module missing)**

Run: `cd frontend && npx vitest run src/views/__tests__/SettingsView.spec.ts`
Expected: FAIL — cannot resolve `../SettingsView.vue`.

- [ ] **Step 3: Build the view**

Create `frontend/src/views/SettingsView.vue`:

```vue
<script setup lang="ts">
/**
 * Settings: choose which metadata fields show on the dashboard tiles.
 * GOV.UK "select all that apply" checkboxes + save; the saved set lives
 * per-user on the server (PUT /api/settings) and in the auth store.
 */
import { ref } from 'vue'
import GovCheckboxes from '@/components/govuk/GovCheckboxes.vue'
import GovButton from '@/components/govuk/GovButton.vue'
import GovNotificationBanner from '@/components/govuk/GovNotificationBanner.vue'
import { DASHBOARD_FIELDS, updateSettings, type DashboardField } from '@/api/settings'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()

const items = DASHBOARD_FIELDS.map((field) => ({ value: field.value, text: field.text }))

// Seed the checkbox model from the store's current preferences.
const selected = ref<string[]>([...auth.dashboardFields])

const saved = ref(false)
const errorMessage = ref<string | null>(null)
const saving = ref(false)

async function onSubmit(): Promise<void> {
  saving.value = true
  saved.value = false
  errorMessage.value = null
  try {
    const fields = selected.value as DashboardField[]
    const result = await updateSettings({ dashboard_fields: fields })
    auth.applyPreferences(result)
    // Reflect the server-cleaned set back into the form.
    selected.value = [...result.dashboard_fields]
    saved.value = true
  } catch {
    errorMessage.value = 'Sorry, your settings could not be saved. Try again.'
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <h1 class="govuk-heading-xl">Settings</h1>

  <GovNotificationBanner
    v-if="saved"
    variant="success"
    data-testid="settings-saved"
  >
    <p class="govuk-notification-banner__heading">Your settings have been saved.</p>
  </GovNotificationBanner>

  <form @submit.prevent="onSubmit">
    <GovCheckboxes
      id="dashboard-fields"
      legend="Dashboard tile fields"
      hint="Select all that apply. The document title and thumbnail are always shown."
      :items="items"
      :error-message="errorMessage ?? undefined"
      v-model="selected"
      small
    />
    <p v-if="errorMessage" class="govuk-visually-hidden" data-testid="settings-error">
      {{ errorMessage }}
    </p>
    <GovButton type="submit" :disabled="saving">Save changes</GovButton>
  </form>
</template>
```

(Note: `GovCheckboxes` already renders the error inside the form group; the extra hidden `<p data-testid="settings-error">` gives the test a stable hook. Keep it — it is screen-reader-hidden and harmless.)

- [ ] **Step 4: Add the route**

In `frontend/src/router/index.ts`, add to `routes` (after the `upload` route, `:32`):

```ts
  {
    path: '/settings',
    name: 'settings',
    component: () => import('../views/SettingsView.vue'),
  },
```

- [ ] **Step 5: Add the nav link**

In `frontend/src/App.vue` `navItems` (`:21-35`), add before `{ text: 'Sign out' }`:

```ts
    { text: 'Settings', to: '/settings', active: route.name === 'settings' },
```

- [ ] **Step 6: Run — expect PASS**

Run: `cd frontend && npx vitest run src/views/__tests__/SettingsView.spec.ts`
Expected: PASS (both cases).

- [ ] **Step 7: Commit**

```bash
git add frontend/src/views/SettingsView.vue frontend/src/router/index.ts frontend/src/App.vue frontend/src/views/__tests__/SettingsView.spec.ts
git commit -m "feat(web): dashboard preferences settings page"
```

---

## Task 8: Render tile fields by preference

**Files:**
- Modify: `frontend/src/views/DocumentListView.vue` (script + template)
- Modify: `frontend/src/views/__tests__/DocumentListView.spec.ts` (`makeItem` + new cases)
- Modify: `frontend/src/styles/main.scss` (tag-chip wrap, if needed — optional)

- [ ] **Step 1: Write the failing dashboard tests**

In `frontend/src/views/__tests__/DocumentListView.spec.ts`:
1. Add `amount_total: null, currency: null` to the `makeItem` defaults (`:18-37`) so the type matches.
2. Add a helper to seed prefs and new cases. The view reads `useAuthStore().dashboardFields`, so set `auth.user` in the test pinia before mounting. Add:

```ts
import { useAuthStore } from '@/stores/auth'

function seedPrefs(fields: string[]): void {
  const auth = useAuthStore()
  auth.user = {
    id: 1, username: 'a', display_name: 'A',
    preferences: { dashboard_fields: fields as never },
  }
}

it('renders only the toggled-on fields', async () => {
  seedPrefs(['kind'])  // sender OFF, tags OFF, date OFF
  listResponse = () =>
    jsonResponse(listBody([makeItem({ sender: { id: 3, name: 'Eneco' }, document_date: '2026-05-15' })]))
  // mount via the file's existing harness (see other tests), then:
  await flushPromises()
  expect(wrapper!.text()).toContain('Invoice')
  expect(wrapper!.text()).not.toContain('Eneco')
  expect(wrapper!.text()).not.toContain('15 May 2026')
})

it('caps tag chips with a +N overflow', async () => {
  seedPrefs(['tags'])
  listResponse = () =>
    jsonResponse(listBody([makeItem({
      tags: [
        { slug: 'a', name: 'A' }, { slug: 'b', name: 'B' }, { slug: 'c', name: 'C' },
        { slug: 'd', name: 'D' }, { slug: 'e', name: 'E' }, { slug: 'f', name: 'F' },
      ],
    })]))
  await flushPromises()
  expect(wrapper!.text()).toContain('+2')  // 6 tags, MAX 4 shown
})
```

Mirror the mount/await pattern the other tests in this file already use (they set `listResponse` then mount in `beforeEach`/per-test — follow that exact structure; `seedPrefs` must run before mount).

- [ ] **Step 2: Run — expect FAIL**

Run: `cd frontend && npx vitest run src/views/__tests__/DocumentListView.spec.ts`
Expected: FAIL — fields render unconditionally today (sender/date show even when off; no `+N`).

- [ ] **Step 3: Wire the preference set into the script**

In `frontend/src/views/DocumentListView.vue` `<script setup>`:
- Import the store and prefs: add `import { useAuthStore } from '@/stores/auth'` and `import type { DashboardField } from '@/api/settings'`.
- After `const router = useRouter()` (`:33`), add:

```ts
const auth = useAuthStore()
const MAX_TAGS = 4
function shows(field: DashboardField): boolean {
  return auth.dashboardFields.includes(field)
}
function formatAmount(item: DocumentListItem): string | null {
  if (item.amount_total === null) return null
  if (item.currency) {
    try {
      return new Intl.NumberFormat('en-GB', { style: 'currency', currency: item.currency }).format(
        Number(item.amount_total),
      )
    } catch {
      return `${item.currency} ${item.amount_total}`
    }
  }
  return item.amount_total
}
```

- [ ] **Step 4: Gate each field in the template**

In `DocumentListView.vue` template, replace the meta block (`:261-272`) with preference-gated rendering, in canonical order (tags-row: kind, language, status, file type; then sender; date; amount; then tag chips):

```vue
          <p class="govuk-body-s app-doc-card__meta">
            <GovTag v-if="shows('kind') && item.kind" colour="blue">{{ item.kind.name }}</GovTag>
            <GovTag v-if="shows('language') && item.language !== 'unknown'" colour="grey">
              {{ languageName(item.language) }}
            </GovTag>
            <template v-if="shows('status')">
              <GovTag v-if="item.status === 'failed'" colour="red">Failed</GovTag>
              <GovTag v-else-if="item.status !== 'indexed'" colour="yellow">Processing</GovTag>
            </template>
            <GovTag v-if="shows('file_type')" colour="grey">{{ fileTypeLabel(item) }}</GovTag>
            <span v-if="shows('sender') && item.sender" class="app-doc-card__sender">
              {{ item.sender.name }}
            </span>
            <span v-if="shows('date') && item.document_date" class="app-doc-card__date">
              {{ formatDate(item.document_date) }}
            </span>
            <span v-if="shows('amount') && formatAmount(item)" class="app-doc-card__amount">
              {{ formatAmount(item) }}
            </span>
          </p>
          <p
            v-if="shows('tags') && item.tags.length"
            class="govuk-body-s app-doc-card__tags"
            data-testid="doc-tags"
          >
            <GovTag v-for="tag in item.tags.slice(0, MAX_TAGS)" :key="tag.slug" colour="grey">
              {{ tag.name }}
            </GovTag>
            <span v-if="item.tags.length > MAX_TAGS" class="app-doc-card__tags-more">
              +{{ item.tags.length - MAX_TAGS }}
            </span>
          </p>
```

- [ ] **Step 5: (Optional) style the new rows**

If tag chips crowd, add to `frontend/src/styles/main.scss` near the existing `.app-doc-card__meta` rules:

```scss
.app-doc-card__tags {
  display: flex;
  flex-wrap: wrap;
  gap: govuk-spacing(1);
  margin-top: govuk-spacing(1);
}
.app-doc-card__amount {
  font-variant-numeric: tabular-nums;
}
```

(Use the spacing helper already used in that file; if `app-doc-card__meta` uses a different gap idiom, match it.)

- [ ] **Step 6: Run dashboard + full unit suite — expect PASS**

Run: `cd frontend && npx vitest run`
Expected: PASS (new cases + no regressions).

- [ ] **Step 7: Type-check & lint**

Run: `cd frontend && npm run type-check && npm run lint`
Expected: no errors. (Confirms the `DocumentDetail` field removal in Task 5 and the new types are consistent.)

- [ ] **Step 8: Commit**

```bash
git add frontend/src/views/DocumentListView.vue frontend/src/views/__tests__/DocumentListView.spec.ts frontend/src/styles/main.scss
git commit -m "feat(web): render dashboard tile fields per user preference"
```

---

## Task 9: e2e — set a preference, see the tile change

**Files:**
- Modify: `frontend/e2e/library.spec.ts` (add a test; reuse its login/setup helpers)

- [ ] **Step 1: Write the e2e test**

Append to `frontend/e2e/library.spec.ts` (mirror the file's existing login helper and selectors). The flow: log in → go to `/settings` → uncheck "Correspondent" → save → return to `/` → assert a tile no longer shows its sender. Skeleton:

```ts
test('dashboard reflects metadata preferences', async ({ page }) => {
  // (reuse this file's existing sign-in helper/fixture)
  await page.goto('/settings')
  await page.getByLabel('Correspondent').uncheck()
  await page.getByRole('button', { name: 'Save changes' }).click()
  await expect(page.getByText('Your settings have been saved.')).toBeVisible()

  await page.goto('/')
  // With sender off, the first card's sender line is gone but its title remains.
  const firstCard = page.locator('.app-doc-card').first()
  await expect(firstCard.locator('.app-doc-card__sender')).toHaveCount(0)
})
```

Adjust selectors/login to match the file's conventions (it already signs in and seeds documents — read the top of the file first).

- [ ] **Step 2: Run the e2e suite locally (if the stack is up)**

Run: `cd frontend && npm run test:e2e` (requires the compose stack on :8000 and the preview server, per `ci.yml`).
Expected: PASS. If the stack isn't running locally, note that CI's `e2e` job will exercise it; do not skip writing the test.

- [ ] **Step 3: Commit**

```bash
git add frontend/e2e/library.spec.ts
git commit -m "test(e2e): dashboard reflects metadata preferences"
```

---

## Task 10: Documentation + journal

**Files:**
- Modify: `docs/api.md` (settings endpoints, `/auth/me` preferences, new list-item fields)
- Modify: `docs/frontend.md` (Settings view + auth-store preferences)
- Create: `journal/260613-dashboard-metadata-preferences.md`

- [ ] **Step 1: Update `docs/api.md`**

Add a "Settings" subsection documenting `GET /api/settings` and `PUT /api/settings` (body `{"dashboard_fields": [...]}`, the eight valid keys, unknown-key-drop behaviour, default set). Note that `GET /api/auth/me` now returns a `preferences` object, and that the document list item now includes `amount_total`/`currency`. Match the document's existing heading depth and tone.

- [ ] **Step 2: Update `docs/frontend.md`**

Document the `/settings` route + `SettingsView`, the `DASHBOARD_FIELDS` source of truth in `api/settings.ts`, and the auth store's `dashboardFields`/`applyPreferences`. Note the canonical tile field order and the tag-overflow `+N` cap.

- [ ] **Step 3: Write the journal entry**

Create `journal/260613-dashboard-metadata-preferences.md` capturing: the JSONB-column decision (vs. table), the absent-vs-empty resolution rule, that the importer-carried tags (incl. storage-path tags) now surface on tiles, and the GOV.UK note (settings page is on-pattern; the tile/card is a bespoke extension).

- [ ] **Step 4: Commit**

```bash
git add docs/api.md docs/frontend.md journal/260613-dashboard-metadata-preferences.md
git commit -m "docs: dashboard metadata preferences (api, frontend, journal)"
```

---

## Final verification

- [ ] **Backend:** `uv run ruff check . && uv run ruff format --check . && uv run coverage run -m pytest && uv run coverage report`
- [ ] **Frontend:** `cd frontend && npm run lint && npm run type-check && npm run test:unit -- --run && npm run build`
- [ ] **Compose smoke (optional, matches CI):** `docker compose up -d --build` then confirm `/healthz` and a login; `docker compose down -v`.
- [ ] **Open a PR** from `feat/dashboard-metadata-prefs` once green.

## Notes for the implementer
- DRY: the eight field keys exist once on each side (`DashboardField` enum in Python, `DASHBOARD_FIELDS` in TS). Don't scatter string literals — import them.
- YAGNI: no per-field ordering, no per-view configs, no density controls.
- The list API change (Task 4) is what makes the `amount` field renderable without a per-tile fetch — it is a prerequisite for Task 8's amount rendering.
- Render order on the tile is fixed (Task 8 template order); the checkbox order (Task 7) and `DASHBOARD_FIELDS` order are the same canonical order.

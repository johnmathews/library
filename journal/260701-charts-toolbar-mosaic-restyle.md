# Charts toolbar restyle to the mosaic field pattern

Date: 2026-07-01

## 1. Context

The `/charts` control bar (Time range · From · To · Group by) looked cramped and inconsistent: the
From/To pickers used `AppDateInput` (three tiny Day/Month/Year text boxes each) next to two dropdowns
that used a different label style. The user pointed at the sister `journal/webapp` (mosaic) Search
toolbar as the target — native date inputs with uppercase-xs labels in one aligned row.

## 2. Change

Restyled the shared presentational component `frontend/src/components/charts/ChartControls.vue`:

- From/To are now native `<input type="date" class="form-input">`. The charts state
  (`customFrom`/`customTo` in `useChartsTimeframe`) is already ISO `yyyy-mm-dd`, so native inputs bind
  directly — no composable/logic change. Empty value emits `null`.
- All four controls sit in a `flex flex-wrap items-end gap-3` row, each under the shared mosaic label
  `block text-xs uppercase text-gray-600 dark:text-gray-300 font-semibold mb-1`.
- Reused the existing `.form-input`/`.form-select` classes (already carry border/bg/rounded-lg/dark
  mode), so no new CSS and no new dependency.
- `data-testid`s and the emit contract (`select-timeframe` / `set-custom` / `update:grouping`) are
  unchanged. `AppDateInput` is untouched (still used by `DocumentFilterBar.vue`).

Updated `ChartControls.spec.ts`: the datepicker-edit test now sets the native input value; added a
clear-to-null case.

## 3. Verification

- `npx vitest run` charts specs — 15/15 pass.
- `npm run lint`, `npm run build` (vue-tsc + Vite) — clean.
- Visual check via a throwaway isolated-component preview page (since local backend/login was not
  running): light + dark both render the aligned mosaic row with native calendar inputs, matching the
  journal reference. Preview harness removed after review.

## 4. Notes

Captured the broader mosaic UI conventions (native inputs, shared `.form-*`/`.btn` classes,
uppercase-xs label + `items-end gap-3` row, single violet accent, `data-testid` naming) in agent
memory as the standard to build future library views to.

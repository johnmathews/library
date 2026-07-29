# The e2e job that passed without running, and 172 aria attributes nobody guarded

W16 and W26. Both are gates over work that already existed and had nothing protecting it.

## 1. W16 — green having launched nothing

All 19 specs opened with `test.skip(!BASE_URL, 'E2E_BASE_URL is not set …')`. That is right on
a laptop and a hole in CI, because **`playwright test` exits 0 when every test skips** and
there is no `--fail-on-skip`. Delete `E2E_BASE_URL` from the workflow and the job reports green
having launched nothing — with nothing in the output distinguishing "the stack was fine and
everything passed" from "the stack was never there".

Same shape as W21's RapidOCR skip and the golden-corpus tier. A skip is reported as success,
so any gate whose failure mode is "skip" is not a gate.

The fix splits behaviour by environment, because the two environments genuinely want different
things: `requireStack()` **throws** when `CI` is set and skips otherwise. The local ergonomics
are the reason the original gate existed and they are preserved exactly.

**Demonstrated, per the acceptance criterion, without needing a throwaway branch** — the throw
happens at collection, so no stack is required:

```
CI=1,  no E2E_BASE_URL  -> exit 1     (was: green)
local, no E2E_BASE_URL  -> 32 skipped, exit 0
```

Then a second, independent guard: `scripts/assert-e2e-ran.mjs` parses the JSON report and fails
below a floor of 40 executed tests, or on any skip mentioning `E2E_BASE_URL`. Not redundant
with the throw — a reporter change, a `--grep` matching nothing, a project-filter typo or a
spec that fails to collect all yield a green run with too few tests, and none of those trip a
module-scope throw. Four cases verified: all-skipped → 1, too-few → 1, missing report → 2,
unparseable → 2, healthy → 0. Missing/unparseable is deliberately a *different* exit code,
because "no report" and "nothing wrong" must not be confusable.

`npm run test:e2e` is untouched; CI calls the new `test:e2e:ci`.

## 2. W26 — the plan's cost estimate was wrong

The a11y care in this app is real — 172 `aria-*`, 54 `role=`, a native `<dialog>` with focus
restore — and nothing protected it. The plan proposed adding
`eslint-plugin-vuejs-accessibility` **and** moving vue's config from `flat/essential` to
`flat/recommended`, calling both "near-zero cost".

Measured, the second is not:

| | violations |
| --- | --- |
| formatting rules from `flat/recommended` | **1,598** |
| real a11y rules from the plugin | **55** |

`vue/html-indent` alone is 691. Taking `flat/recommended` would have meant an enormous
formatting diff that buried the 55 findings the plugin exists to surface. So `flat/essential`
stays and only the a11y plugin lands. **Correction to the plan, recorded.**

### Both named defects were real

- **`ThreadActionsMenu.vue`** announced `role="menu"` with two `role="menuitem"` children and
  implemented *none* of the ARIA menu contract — no arrow keys, no roving tabindex, no focus-in
  on open (grep for `@keydown` in that file returns nothing). It told assistive technology to
  expect navigation that does not exist. Removed the roles: it now announces as two ordinary
  buttons, which is honest and already works. `AppPopover.vue` implements the contract properly
  and is the model if the roles are ever wanted back.
- **`AppSidebar.vue`** made the persistent wordmark an `<h1>`, so every authenticated page had
  two competing top-level headings alongside `PageHeader`'s. A screen-reader heading list led
  with "LIBRARY" rather than the page. Now a `<p>` with identical classes.

### The rule that is wrong for this codebase

`no-redundant-roles` flagged `role="list"` on two `<ul>`s as redundant. It is not:
**Tailwind preflight sets `list-style: none`** (preflight.css:200) and Safari drops list
semantics from an unmarkered list, so the explicit role is what restores them for VoiceOver.
Obeying the linter would have silently removed list semantics from two lists to satisfy a
static check.

That one is off permanently with the reason at the config site. The genuinely redundant case in
the same rule — `<fieldset role="group">`, where nothing strips the implicit semantics — was
fixed rather than suppressed. The remaining four rules are off with a named exit each, matching
W15's ratchet shape.

One test broke: `AppDateInput.spec.ts` asserted `fieldset[role="group"]` exists. It was pinning
the *mechanism* (an explicit attribute) rather than the outcome (grouped semantics, which
`<fieldset>` provides natively), so it now asserts the element and that the role is absent.

**Grading: confirmed by seeded defect.** An `<img>` with no `alt` and a `role="nonsense-role"`
both red `npm run lint`; removing them restores exit 0. The gate is live, not decorative.

## 3. Result

1044 frontend tests pass, eslint and vue-tsc clean, 1427 backend tests unaffected. W22 and W23
are deferred to a follow-up: W23's acceptance criterion requires demonstrating a layout spec
fails when a real fix is reverted, which needs the compose stack running.

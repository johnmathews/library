# Library on the Quiet design system

> Purpose
>
> Records how Library adopted Family Finances' "Quiet" design system, the decisions the owner
> made and the ones made on the owner's behalf, what the restyle found, and the guards it left
> behind. The living description is [docs/brand.md](../docs/brand.md).

**Date:** 2026-11-21
**Branch:** `brand-design-system`

## 1. The request

In the owner's words: give Library "a good favicon and logo", and "bring it more in line with the
principles and design choices of the finances application (primarily) and the journal insights app
(secondary)".

The [brand design](../docs/superpowers/specs/2026-11-21-brand-design.md) answers it by taking Quiet
whole: the same token values, the same violet, Inter, cards without outlines, sentence case and
colour only where it means something. Library keeps the Mosaic sidebar shell it shares with journal
insights. The design system was published as a claude.ai artifact, with its source in
`brand/design-system/`.

## 2. The calls made on the owner's behalf

The spec's §8 listed seven calls so each could be vetoed. The owner approved all of them ("approve
all, but show me some mockups as artifacts"):

1. The same values as Finances, with no Library variant.
2. The primary button darkens to `brand-fill` (`#5d47de`), taking white on it from 3.66:1 to 6.14:1.
   Violet text moves to `brand` for the same reason.
3. Tags, projects and matters lose their ten hashed colours and become neutral chips.
   `tagColour` was deleted.
4. Finished states are neutral with a check icon. Green stays money-in only.
5. The six page tones stay, with Quiet's canvas `#f4f5f7` as the new default.
6. Inter is self-hosted as Inter Variable, replacing the Google Fonts import.
7. Document-type colours stay a feature, and their shape on an outline-less tile gets its own
   mockup.

## 3. The icon: B over the recommended A

The [app-icon mockup](../brand/design-system/mockups/261121-app-icon/README.md) offered three marks
on Finances' violet square:

- **A**, a solid sheet under a lens, recommended because a filled mark holds up best at 16px and
  matches Finances' filled bars
- **B**, a page drawn in round-capped lines with a folded corner, under a lens
- **C**, writing inside a lens, which sat too close to the sidebar's own Search icon

The owner picked B. It reads most as a document at 64px and up, and is the thinnest of the three at
16px. The sidebar logo (`AppLogo.vue`) is the same square beside "Library", and it also heads the
sign-in and OAuth consent pages.

## 4. Document type colours: a band across the top

Quiet's cards have no outline, so the old coloured tile border had nothing to paint. The
[documents mockup](../brand/design-system/mockups/261121-documents/README.md) offered a 4px band
across the top (A, recommended), a stripe down the left (B) and a dot in the type chip (C). The owner
took A, and also chose the chart palette `category-1…5` as the default type colours. Colours an
owner had already picked per type are kept.

## 5. Scope and shipping

The restyle covers every screen: the shell, every view, the `App*` components and the spending
charts, in both themes. It ships as one pull request. A partly restyled app would put two colour
systems on one page, and each pull request costs a full CI run.

The restyle followed spec §3.2 for colour meanings. Yellow means the owner has something to do, red
means something failed, and income and deficit figures appear only on the spending and payments
views. Violet marks what can be acted on, and everything else is ink on tile. No button is red any
more. `AppButton`'s `warning` variant renders as the outline button, and a destructive action's
confirming button in `ConfirmDialog` is the primary.

## 6. The focus ring

The spec copied Finances' focus ring, a 2px `currentColor` outline at a 2px offset. The offset draws
the ring on the surface around the control, so it takes the control's text colour onto the card
behind it. On a violet primary button that is the white label colour on a white card, measured at
1.00:1: no visible ring at all.

The app's ring is now the `focus` role (ink), which clears 3:1 on canvas, panel and tile in both
themes. `main.css` also removes `@tailwindcss/forms`' blue focus ring. Finances' `theme.css` has the
same `currentColor` rule and very likely the same flaw on its primary buttons. That was not measured
there.

## 7. The palette folds at five

Library's chart palette had six slots. It now uses the design system's five, `category-1` to
`category-5`, read at runtime through `chartColour()` in `utils/splitPalette.ts`. Values past five
fold into an "Other (N)" band in `category-other`. Each slot keeps the hex the old palette stored as
its identity, so an override picked before the change keeps its slot without a data migration. The
old sixth slot (olive) has no successor and paints as stored, like any colour from outside the
palette.

## 8. The guards

Each guard below was seen failing on a planted violation before the real tree turned it green:

| Guard | Holds |
| --- | --- |
| `scripts/check_design_system.py --complete`, in `make lint` and CI's backend Lint step | Token shapes, contrast in both themes, the component inventory, banned words |
| `tests/test_design_tokens_current.py` | `tokens.css` is a fresh generation of `tokens.json` |
| `frontend/src/__tests__/no-raw-colour.spec.ts` | No Tailwind palette class, white or black colour class, or raw hex outside an allow-list of data files |
| `frontend/src/assets/__tests__/canvas-tones.spec.ts` | Each page tone's swatch equals its CSS, and the default equals the system's canvas |
| `frontend/src/__tests__/app-icon.spec.ts` | Icon sizes and colours against `tokens.json` |
| `frontend/e2e/a11y.spec.ts` | axe WCAG 2 A/AA on sign-in and seven screens, light and dark, chromium only |

The colour spec exists because a palette class bypasses the contrast gate, and two AA failures had
shipped that way before the restyle. The a11y spec's first test fails if flipping `.dark` changes no
token, so a dark audit cannot silently re-run light.

## 9. The wrap-up

Same day, during `/done`, before the PR:

- **The local CI mirror ran on fresh stacks.** Backend suite 2584 passed at 95% coverage, both
  engine-skip floors and mypy clean; frontend 122 files and 1539 tests, type-check and eslint
  clean; e2e desktop 66, mobile 48, tablet 44 executed, each over its floor; the compose smoke
  check served the SPA. Confirmed by the runs themselves.
- **The axe spec caught a real defect on a fresh stack.** On an empty library, "Upload your first
  document" sat inside a sentence told apart only by colour (`link-in-text-block`). The earlier
  local run had a seeded library and never showed that empty state. Four links inside sentences
  are now underlined, as the TextLink card says. Confirmed: red on the fresh stack, green after.
- **An independent code review found three more.** Keyboard focus was invisible on every select
  and textarea: `@tailwindcss/forms` puts its base rule after ours at equal specificity. Confirmed
  in Chromium (`rgba(0, 0, 0, 0)` outline), fixed with a `:root` prefix, and pinned by a new e2e
  test that tabs onto a select; the test was seen red without the fix and green with it. Inline
  "Confirm delete" buttons had become identical to "Cancel" once `warning` stopped being red; the
  confirming buttons are now the primary. The CI path filter counted all of `brand/**`, so a
  mockup-only commit would have run e2e and the image build; it now counts only the design
  system's source.
- **An independent docs audit** fixed the top-level README (still described Mosaic), two
  incomplete `Covers:` lines, a garbled sentence in `docs/frontend.md` and a stale §6.3 in
  `docs/brand.md`. The design system's focus-ring text, `docs/facets.md` and `docs/architecture.md`
  were corrected and the artifact republished.

- **CI's e2e failed on the runner, not the change.** All three shards stopped at "Install
  Playwright browsers" with "Playwright does not support chromium on ubuntu26.04-x64" (webkit the
  same): `ubuntu-latest` had moved to Ubuntu 26.04 since `main`'s last run on 2026-11-07. Confirmed
  from the job logs of run 44952691488. The e2e job is now pinned to `ubuntu-24.04`, with a comment
  saying when to move back.

## 10. Open

- #235: the raw-colour guard misses `rgb()` and 3- or 8-digit hex; two scoped styles already use
  `rgb()`.
- #236: the ChartStyle card describes an empty-month stub and euro ticks the charts do not draw.
- Family Finances very likely has the same `currentColor` focus-ring flaw (its `theme.css` uses
  `outline: 2px solid currentColor`). Suspected, not measured there; not filed, since it is
  another repository.

## What is deliberately not done

- No component kit (shadcn-vue, Reka UI): the `App*` set covers the controls, as in Family Finances.
- No backend or data change. The canvas tone stays the stored string it was; "neutral" now means
  Quiet's canvas rather than gray-200.
- Charts show the same things as before (the plan's non-goal); only their colours and the fold
  point changed.
- Journal insights was not restyled. It shares the Mosaic shell Library keeps.

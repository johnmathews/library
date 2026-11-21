# Brand and design system

**Status:** active. **Supersedes:** none. **Last updated:** 2026-11-21 (first version).
**Last verified:** 2026-11-21 — method: read against the working tree. `check_design_system.py brand/design-system --complete` exits 0, both scripts are `cmp`-equal to the skill's, and each path, test and command named here was opened. The guards' red runs were not repeated in this pass.
**Covers:** brand/design-system, scripts/check_design_system.py, scripts/design_preview.py, frontend/src/assets/tokens.css, frontend/public/favicon.svg, frontend/public/app-icon.svg, frontend/scripts/build-app-icons.sh, frontend/src/components/layout/AppLogo.vue, frontend/index.html

> Purpose
>
> Says where Library's look and voice are decided, how the design system is kept, and how to
> change it. Read it before any visual change: a screen, a component, a colour or an icon.

## 1. The direction

The direction is Family Finances' "Quiet", taken whole: "functional and clear, but also friendly
and welcoming". Library uses the same token values in both themes, the same violet and the same
Inter, so the two apps read as one family. It keeps the Mosaic sidebar-and-header shell that both
apps share with journal insights.

The friendliness comes from subtraction:

- cards without outlines, standing off the page by fill and shadow
- sentence-case labels and plain words
- colour only where it means something (§6.2)

Library differs in content. Its first impression is the grid of document tiles, where Finances
leads with a figure. The reasons, the rules and every call made on the owner's behalf are in the
[brand design](superpowers/specs/2026-11-21-brand-design.md).

## 2. The design system

The design system is a claude.ai artifact, https://claude.ai/artifact/GrPWiVTCFuqF9xZn1gw3K3. It
holds the brand book, the voice, the tokens in two themes, and 24 components with live previews and
guidelines, plus a cover. An agent designing for Library reads its README first.

Its source lives in this repository, at the paths the artifact serves:

| Path | Contents |
| --- | --- |
| `brand/design-system/SYSTEM.md` | The artifact's URL |
| `brand/design-system/checks.json` | The gate's settings: required components, Library's stricter contrast pairs, banned words |
| `brand/design-system/project/design-system.json` | The artifact's index |
| `brand/design-system/project/tokens.json` | Every colour, type style, space, radius and shadow, light and dark |
| `brand/design-system/project/README.md`, `voice.md` | The brand book and the voice |
| `brand/design-system/project/components/<Name>/` | Each component's `README.md` and `preview.html` (`Cover` has a preview only) |
| `brand/design-system/project/fonts/`, `licenses/` | Inter Variable and IBM Plex Mono, with their OFL licences |
| `brand/design-system/mockups/<yymmdd>-<slug>/` | Mockups and their decisions (§4) |

`scripts/check_design_system.py brand/design-system --complete` is the gate. It checks the token
shapes, contrast in both themes, the component inventory and banned words. The contrast pairs in
`checks.json` ask 5:1 for text, 4.5:1 for accents and 3:1 for control edges and chart colours.
`make lint` runs it, and so does CI's backend `Lint` step. In CI's path filters,
`brand/design-system/project/**` and `checks.json` count as a backend change (the gate and the
token test read them) and `tokens.json` also as a frontend change (two frontend specs read it). A
change to `mockups/` alone runs neither: nothing executes it.

`python3 scripts/design_preview.py brand/design-system` builds an offline gallery into the
git-ignored `brand/design-system/preview/`. The same script generates the app's `tokens.css` (§3).

Both scripts are copies of the design-system skill's. They stay byte-identical so their version
check can say when a copy is behind, and `pyproject.toml` excludes them from ruff for that reason.
A fix goes to the skill first, then gets copied here.

## 3. Changing it

1. Edit the files under `brand/design-system/project/`.
2. If `tokens.json` changed, regenerate the app's tokens:

   ```bash
   python3 scripts/design_preview.py brand/design-system \
     --tokens-css frontend/src/assets/tokens.css --font-base ./fonts
   ```

   `tests/test_design_tokens_current.py` fails until the two match.
3. If `brand-fill` or `on-brand` changed, update `frontend/public/favicon.svg` and `app-icon.svg`,
   then run `frontend/scripts/build-app-icons.sh` (§5). `app-icon.spec.ts` fails until the icon
   follows.
4. Run `make lint`, the backend tests and `npm run test:unit` in `frontend/`.
5. Publish only the changed files to the artifact with `root: brand/design-system`. Send
   `design-system.json` only when its own keys change, and last.

Edits made on the artifact's page are not in the repository. Read the changed files back before
editing here.

## 4. Mockups

A visual change starts as a mockup and waits for the owner's pick before any code. Each question
gets a folder in `brand/design-system/mockups/<yymmdd>-<slug>/`:

- `index.html`: the options, built from the tokens, in both themes
- `README.md`: the question, the options, the decision and the link to the private artifact it was
  published as

The READMEs are decision records and age like the journal. `dist/` folders are builds and are not
committed. The design-system skill's `references/mockups.md` has the loop and its commands.

| Mockup | Question | Decision |
| --- | --- | --- |
| `261121-app-icon/` | The mark on the app icon, favicon and sidebar logo | B, a page drawn in lines under a lens |
| `261121-documents/` | How a document type's colour shows on a tile without an outline | A, a 4px band across the tile's top |

## 5. The icon

The mark is a page drawn in white round-capped lines, with a folded corner and two lines of
writing, and a lens over its lower corner. It sits on the violet `brand-fill` square, the same
square as Family Finances' icon.

| File | Use |
| --- | --- |
| `frontend/public/favicon.svg` | The rounded square, for browser tabs |
| `frontend/public/app-icon.svg` | The mark full-bleed, inside the central 80% that Android's maskable crop keeps |
| `favicon.ico`, `apple-touch-icon.png`, `icons/icon-192.png`, `icon-512.png`, `icon-512-maskable.png` | Rasters built from the two SVGs |
| `frontend/src/components/layout/AppLogo.vue` | The sidebar logo: the square at 32px beside "Library" |

`frontend/scripts/build-app-icons.sh` builds the rasters. It needs `rsvg-convert` (librsvg) and
ImageMagick 7 (`magick`), and writes `favicon.ico` at 16, 32 and 48px, `apple-touch-icon.png` at
180px and the manifest's three PNGs. The rasters are committed, so the image build needs neither
tool.

`AppLogo` draws the favicon's square inline in the `brand-fill` and `on-brand` roles. The name
hides when the desktop sidebar collapses, and the square stays. `LoginView` and `OAuthConsentView`
show the logo too. The manifest's `theme_color` and `background_color` and the `theme-color` meta
tag are the canvas, `#f4f5f7`. `frontend/src/__tests__/app-icon.spec.ts` checks each raster's size
and both SVGs' colours against `tokens.json`.

## 6. Where the app stands

As of 2026-11-21 every screen is on the system. [frontend.md](frontend.md) §1.2 has the wiring.

### 6.1 Wiring

- `frontend/src/assets/tokens.css` is generated from `tokens.json` (§3) and declares the roles,
  light on `:root` and dark on `.dark`.
- `main.css` maps each role to a Tailwind utility (`bg-panel`, `text-ink`, `bg-brand-fill`,
  `text-attention` and the rest). A component paints with those utilities and never with a palette
  class such as `text-gray-500`.
- Inter Variable and IBM Plex Mono are self-hosted from `frontend/src/assets/fonts/`.
- Settings → Appearance's page tones are values of `--canvas`. The default, "neutral", is Quiet's
  `#f4f5f7`.

### 6.2 What a colour means

A meaningful colour means one thing ([spec](superpowers/specs/2026-11-21-brand-design.md) §3.2):

| Colour | Means | Where |
| --- | --- | --- |
| `attention` on `attention-tint` (yellow) | The owner has something to do | Needs review, held emails, the review queue bar |
| `error-ink` on `deficit-tint` (red) | Something failed | Failed jobs, error banners, the error summary |
| `income` / `deficit` figures | Money in, a negative net | The spending and payments views only |
| `category-1` to `category-5`, `category-other` | A series in a chart, or a document type's default colour | Charts, the tile's top band |
| `brand` (violet) | Something that can be acted on | Links, the active sidebar item, the primary button |

Everything else is `ink` on `tile`. That covers tags, projects, matters and topics, which are
neutral chips, and finished states, which `AppBadge` draws neutral with a check icon. No button is
red: `AppButton`'s `warning` variant renders as the outline button, and a destructive action's
confirming button in `ConfirmDialog` is the primary.

### 6.3 The focus ring

The focus ring is a 2px outline at a 2px offset in the `focus` role (ink), in the app and in the
design system's README and component cards. A `currentColor` ring draws on the surface around the
control, so a violet button's white label gave a white ring on a white card. The approved spec
(point-in-time) still says `currentColor`; this section supersedes it.

### 6.4 The guards

| Guard | Fails when |
| --- | --- |
| `scripts/check_design_system.py --complete` | A token pair misses its contrast, a component is missing, a banned word appears |
| `tests/test_design_tokens_current.py` | `tokens.css` differs from a fresh generation |
| `frontend/src/__tests__/no-raw-colour.spec.ts` | A source file uses a Tailwind palette class, a white or black colour class, or a raw hex outside its allow-list of data files |
| `frontend/src/assets/__tests__/canvas-tones.spec.ts` | A page tone's swatch differs from its CSS, or the default differs from `tokens.json`'s canvas |
| `frontend/src/__tests__/app-icon.spec.ts` | An icon file has the wrong size or colours |
| `frontend/e2e/a11y.spec.ts` | axe finds a WCAG 2 A or AA violation on sign-in or seven signed-in screens, in light or dark (chromium only) |

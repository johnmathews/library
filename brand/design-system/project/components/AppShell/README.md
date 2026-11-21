The frame around every signed-in screen: a sidebar of sections with the logo at its top, a header with the page title, and a phone bar below 640px.

## Use

- **Sidebar:** `panel` fill, a `line` hairline on its right edge. The logo first: the AppIcon square at 32px beside "Library" in Inter at 650, 18px, sentence case.
- **Sections, in today's order:** Documents, Search, Upload, New note, Charts, Ask, Jobs, Projects, Matters, then a gap, then Vocabulary, Settings, Admin and Recently deleted. Each is a 16px icon in `ink-secondary` and a `type-body` label at 500 in `ink`, `radius-sm`, 6px by 10px.
- **Active item:** `brand-tint` fill, `brand-on-tint` text and icon at 650. Exactly one is active.
- **Header:** the page title in `type-title-1`, then the view's toolbar (PageHeader), then the background-jobs indicator and the theme toggle.
- **Phone (below 640px):** the sidebar hides behind the menu button in a `panel` bar with the 28px square and the page title at 18px. It opens as a drawer with `shadow-pop`.
- The page is `canvas`, with `space-8` side padding on desktop and `space-4` on a phone.
- The shell is the Mosaic one Library shares with journal insights and Family Finances. Keep its structure; restyle its surfaces.

## States

- Collapsed (at `lg` widths, when the owner asks): icons only, and the logo's square stays.
- The active item never relies on colour alone: it also takes weight 650 and `aria-current="page"`.

## You provide

The current section and the header's toolbar for the screen. Today: `layouts/DefaultLayout.vue`, `components/layout/AppSidebar.vue` (whose "LIBRARY" heading becomes the logo) and `components/layout/AppHeader.vue`.

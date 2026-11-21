The app icon: a page drawn in lines with a lens over its corner, in `on-brand` on a `brand-fill` square, in browser tabs, bookmarks, home screens and the sidebar's logo.

## Use

- **The mark,** on a 32px grid: a page with a folded corner and two lines of writing in round-capped 2.5px strokes, and a lens (radius 4.5) over the lower right corner with a 3px handle. The lens sits on a disc in the square's own colour, so the page's edge stops short of it. The geometry is in this card's preview and in mockup `261121-app-icon`, option B.
- **Favicon:** the rounded square (radius 7 on the 32px grid), `frontend/public/favicon.svg`, with `favicon.ico` (16, 32, 48) beside it.
- **Home screens:** the square full-bleed with the mark at 80%, as `apple-touch-icon.png` (180) and the manifest's `icons/icon-192.png`, `icon-512.png` and `icon-512-maskable.png`. iOS and Android round the corners themselves; Android's maskable crop keeps the central 80%, where the mark sits.
- **The logo:** the square at 32px beside "Library" in Inter at 650, 10px apart. Collapsed, the square alone. On the phone bar, the square at 28px.
- Family Finances uses the same square with its own mark, so the two read as one family side by side.

## States

One state. It does not change with the theme: `brand-fill` is the one accent that does not flip, and the mark on it is 6.1:1 in both. "Library" beside it is `ink`.

## You provide

Nothing. Today the files in `frontend/public/` are a white "L." on black; the restyle redraws all of them from this mark and checks the colours against `tokens.json`.

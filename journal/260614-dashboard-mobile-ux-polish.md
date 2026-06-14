# 260614 — Dashboard & mobile UX polish

A run of incremental UI fixes layered on top of the per-user tile-preview
feature (see `260614-tile-preview-mode.md`), driven by testing on an actual
iPhone. Each change shipped and deployed on its own (CI → ghcr `:latest` →
recreate on the `paperless` LXC) before moving to the next.

## Changes

1. **Tile preview fade.** The full-width crop ended in a hard seam against the
   white card body. Added a `pointer-events-none` bottom gradient overlay
   (`h-8`, `from-transparent to-white dark:to-gray-800`) on the thumbnail box —
   only in `full_width` mode and over a real thumbnail (the letterbox/fallback
   states sit on the gray box, where a white fade would look wrong).

2. **Whole dashboard tile is a click target.** Previously only the title link
   navigated. Used the **stretched-link** pattern: the single title
   `RouterLink` gets `after:absolute after:inset-0` over the now-`relative`
   card. One anchor, no nested links — keeps `?highlight`, middle-click, and the
   existing "exactly one anchor per tile" test contract.

3. **Mobile PDF preview that fits the screen.** The detail page shows the PDF in
   the browser-native `<iframe>`. On iOS Safari the native viewer renders the
   page wider than the viewport and **ignores the `#view=FitH` open-parameter**
   (added first, kept for desktop/Android where it works). Fallback that
   actually works on iOS: on small screens swap the iframe for the **fit-width
   first-page thumbnail** (the existing 480px `thumb.webp`), keeping the iframe
   on `lg+`. Chose the 480px thumbnail (frontend-only) over a new higher-res
   render endpoint — good enough for "see the whole page"; the
   open-in-new-tab link covers reading fine print. A crisp ~1000px render
   endpoint remains the upgrade path if 480px proves too soft.

4. **Tappable mobile preview.** Wrapped that thumbnail in a link that opens the
   PDF (same action/target as the text link) — large tap target, as preferred.

5. **Detail page no longer loads zoomed-in on mobile.** iOS rendered the page
   zoomed because content was wider than the viewport (and the layout's
   `overflow-x-hidden` doesn't reliably contain that on iOS). Root cause:
   grid/flex items default to `min-width:auto` and long unbreakable strings
   (machine-generated underscore titles, URLs/refs in summaries, OCR tokens)
   don't wrap. Fix: `min-w-0` on both detail grid columns and `break-words` on
   the title, metadata values, and OCR `<pre>`, so nothing forces the page
   wider than the screen. Diagnosed by inference (couldn't inspect the live iOS
   DOM); confirmed fixed by the user on-device.

6. **Padlock placeholder for password-protected PDFs.** Encrypted PDFs can't be
   rendered, so they have no thumbnail (and no searchable PDF) — and the bare
   mobile iframe was blank and non-clickable. There's no encryption flag in the
   schema, so the signal is "PDF + `!has_thumbnail`". For that case mobile now
   shows a clickable padlock placeholder (inline heroicons lock SVG, "Protected
   PDF — tap to open") that opens the PDF so the browser prompts for the
   password; the desktop iframe stays (hidden below `lg`) since it can prompt
   inline.

## Notes

- All changes are presentation-layer (Vue templates/classes); no API or schema
  changes. Backend untouched except already-shipped tile-preview work.
- Tests added/updated alongside each change (DocumentListView + DocumentDetailView
  specs): stretched-link contract, responsive iframe/image swap, `#view=FitH`
  iframe src, tappable image link.

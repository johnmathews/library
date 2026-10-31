# Downloads from the iOS home-screen app

**Date:** 2026-10-31
**Branch:** `fix-ios-standalone-download`

## 1. What happened

With Library added to the iPhone home screen, tapping **Download** on a document
opened the file in a full-screen viewer with no button or menu to save it. Swiping
back returned to the app, but the file could not be saved.

This is WebKit's behaviour, not a regression in Library:
[WebKit bug 290847](https://bugs.webkit.org/show_bug.cgi?id=290847) (filed 2025-04,
still open). A home-screen web app has no download manager and no Safari share
button, so an attachment link ends in a viewer that can only display the file.
Every Download link in the app was a plain `<a href>` to an attachment URL, so all
of them hit it.

## 2. What changed

- `components/DownloadLink.vue` replaces the six Download anchors in
  `DocumentDetailView` and `DocumentPdfPreview`. Outside an iOS home-screen app it
  is the same plain link. Inside one (`navigator.standalone === true`, with the Web
  Share API present) a tap fetches the file and calls
  `navigator.share({ files: [file] })`, so the share sheet offers **Save to Files**.
- `utils/contentDisposition.ts` names the shared file from the response header,
  preferring the RFC 5987 `filename*=` form Starlette sends for non-ASCII names.
- `docs/frontend.md` §1.6.1 describes the behaviour.

## 3. Decisions

- **Detect `navigator.standalone`, not `display-mode`.** The manifest says
  `minimal-ui`, which iOS does not implement, and how iOS reports the display mode
  for it is not something any source settled. `navigator.standalone` is the
  iOS-only home-screen flag, so desktop browsers and ordinary Safari keep the
  native download untouched.
- **A second tap instead of prefetching.** iOS rejects `navigator.share` when it
  starts too long after the tap (`NotAllowedError`), and a fetch can take that
  long. Prefetching every document on page load would avoid that but downloads
  files nobody asked for. The link keeps the fetched file and reads
  **Tap again to save**, and the second tap shares it with no fetch.
- **Fail back to the old link.** If the fetch fails, `canShare` refuses the
  file, or `navigator.share` rejects with anything but `NotAllowedError` or
  `AbortError` (a dismissed sheet), the link navigates to the file as before. The
  change can only replace the dead-end viewer, never make a download worse.
- **Reset on `href` change.** The detail view is reused across previous/next
  navigation (the `RouterView` is unkeyed), so the same `DownloadLink` instance
  moves between documents. The code review caught that a kept "Tap again to save"
  file, or a fetch still in flight, would then be shared from the next
  document's page. A watcher on `href` drops the file and bumps a generation
  counter that orphans the fetch.
- **Modifier and non-primary clicks keep their native meaning**, e.g. cmd-click
  on an iPad with a keyboard.
- **HTTPS only, by the platform.** `navigator.share` exists only in a secure
  context. A home-screen app installed from the plain-HTTP LAN address has no
  share API, falls back to the old link and keeps the dead end. Only the HTTPS
  origin gets the fix, and no change in web code can do better.
- **The Open button stays as it is.** It was not reported broken and the user
  chose to leave it.

## 4. Verification and what it cannot prove

Unit specs (18) cover the plain-link path, the share path, the second-tap path,
a dismissed sheet, the three fallbacks, repeated taps, modifier clicks and both
`href`-change cases. A new case in
`e2e/pdf-preview.spec.ts` fakes `navigator.standalone` and the share API against
the real stack and passed on the `chromium`, `mobile-webkit` and `webkit`
projects. That run predates the review fixes, which are unit-covered; CI's e2e
re-runs it on all five projects. Both the unit and e2e share assertions were
mutation-checked red (confirmed). No source confirmed that file sharing works inside an
iOS home-screen app specifically, and Playwright cannot run one, so the real
check is a tap on the phone after deploy.
Whether the share sheet appears inside the real home-screen app is
**strongly supported**, not confirmed.

## 5. What is deliberately not done

- **Open** buttons are unchanged (the user's call). An inline PDF opened from a
  home-screen app can still be a dead end.
- No prefetching of the file on page load, for the reason in §3.
- The kept file in the "Tap again to save" state has no timeout. It is released
  on the next tap, on an `href` change or on unmount.
- While preparing, the label replaces the slot, so the icon buttons briefly lose
  their icon. Accepted for a state that lasts about as long as one fetch.
- The plain-HTTP LAN origin cannot be fixed from web code; see §3.

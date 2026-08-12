/**
 * The anti-flash sidebar seed in `index.html`.
 *
 * The inline script paints `body.sidebar-expanded` before Vue boots, so it
 * has to resolve the collapse preference the *same* way AppSidebar.vue does
 * once it mounts — primary `library:sidebar-expanded` key, legacy bare key,
 * then the `matchMedia('(min-width: 1024px)')` default. Any divergence shows
 * up as the sidebar settling a frame late, which is the flash the script
 * exists to prevent (it long read only the legacy key).
 *
 * These tests extract the real script out of `index.html` and execute it,
 * rather than pattern-matching its source: what matters is the body class it
 * ends up producing, not how it spells the lookup.
 */
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

// Under jsdom import.meta.url is an http:// URL, so resolve from the vitest
// root (vitest.config.ts pins it to frontend/), as pwa.spec.ts does.
const frontendRoot = process.cwd()
const html = readFileSync(join(frontendRoot, 'index.html'), 'utf8')
const sidebarSource = readFileSync(
  join(frontendRoot, 'src', 'components', 'layout', 'AppSidebar.vue'),
  'utf8',
)

/** The one inline, non-module script in index.html — the seed. */
function seedScript(): string {
  const doc = new DOMParser().parseFromString(html, 'text/html')
  const scripts = [...doc.querySelectorAll('script')].filter(
    (s) => !s.getAttribute('src') && s.getAttribute('type') !== 'module',
  )
  expect(scripts).toHaveLength(1)
  return scripts[0]?.textContent ?? ''
}

/** Run the seed with a given viewport match, and report the resulting class. */
function runSeed(wideViewport: boolean): boolean {
  window.matchMedia = ((query: string) => ({
    matches: query.includes('min-width: 1024px') ? wideViewport : false,
    media: query,
    addEventListener: () => {},
    removeEventListener: () => {},
  })) as unknown as typeof window.matchMedia
  document.body.className = ''
  new Function(seedScript())()
  return document.body.classList.contains('sidebar-expanded')
}

const PRIMARY_KEY = 'library:sidebar-expanded'
const LEGACY_KEY = 'sidebar-expanded'

describe('index.html sidebar seed', () => {
  beforeEach(() => localStorage.clear())
  afterEach(() => {
    localStorage.clear()
    document.body.className = ''
  })

  it('uses the same primary storage key as AppSidebar.vue', () => {
    // Pin the literal: the seed and the store must not drift apart.
    expect(sidebarSource).toContain(`SIDEBAR_EXPANDED_KEY = '${PRIMARY_KEY}'`)
    expect(seedScript()).toContain(PRIMARY_KEY)
  })

  it('honours the primary key when it is set', () => {
    localStorage.setItem(PRIMARY_KEY, 'true')
    expect(runSeed(false)).toBe(true)

    localStorage.setItem(PRIMARY_KEY, 'false')
    expect(runSeed(true)).toBe(false)
  })

  it('prefers the primary key over a stale legacy value', () => {
    localStorage.setItem(PRIMARY_KEY, 'false')
    localStorage.setItem(LEGACY_KEY, 'true')
    expect(runSeed(true)).toBe(false)
  })

  it('falls back to the legacy bare key when the primary is unset', () => {
    localStorage.setItem(LEGACY_KEY, 'true')
    expect(runSeed(false)).toBe(true)

    localStorage.setItem(LEGACY_KEY, 'false')
    expect(runSeed(true)).toBe(false)
  })

  it('defaults from the viewport when neither key is set', () => {
    // Matches AppSidebar.vue's `defaultSidebarExpanded()`: expanded on a
    // desktop-width viewport, collapsed below it.
    expect(runSeed(true)).toBe(true)
    expect(runSeed(false)).toBe(false)
  })
})

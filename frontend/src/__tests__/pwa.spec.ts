/**
 * PWA wiring regression test (W16). Lighthouse's installability audit needs
 * a served origin and a headed Chrome run, so it is deliberately not in CI —
 * this file-level test plus the Playwright viewport matrix cover the same
 * regression risk: the manifest stays linked, parseable and complete, and
 * every icon it references actually ships.
 */
import { describe, expect, it } from 'vitest'
import { existsSync, readFileSync } from 'node:fs'
import { join } from 'node:path'

// Under the jsdom environment import.meta.url is an http:// URL, so resolve
// from the vitest root (vitest.config.ts pins it to frontend/).
const frontendRoot = process.cwd()
const html = readFileSync(join(frontendRoot, 'index.html'), 'utf8')
const doc = new DOMParser().parseFromString(html, 'text/html')
const manifestRaw = readFileSync(join(frontendRoot, 'public', 'manifest.webmanifest'), 'utf8')

/** Resolve a root-absolute URL ("/x.png") to a file under public/. */
function publicFile(href: string): string {
  return join(frontendRoot, 'public', href.replace(/^\//, ''))
}

describe('index.html PWA wiring', () => {
  it('links the web app manifest', () => {
    const link = doc.querySelector('link[rel="manifest"]')
    expect(link?.getAttribute('href')).toBe('/manifest.webmanifest')
  })

  it('links an apple-touch-icon that exists in public/', () => {
    const href = doc.querySelector('link[rel="apple-touch-icon"]')?.getAttribute('href')
    expect(href).toBe('/apple-touch-icon.png')
    expect(existsSync(publicFile(href!))).toBe(true)
  })

  it('links SVG and ICO favicons that exist in public/', () => {
    const hrefs = [...doc.querySelectorAll('link[rel="icon"]')].map((l) => l.getAttribute('href'))
    expect(hrefs).toContain('/favicon.svg')
    expect(hrefs).toContain('/favicon.ico')
    for (const href of hrefs) expect(existsSync(publicFile(href!))).toBe(true)
  })

  it('keeps viewport-fit=cover (safe-area insets depend on it)', () => {
    const viewport = doc.querySelector('meta[name="viewport"]')?.getAttribute('content')
    expect(viewport).toContain('viewport-fit=cover')
  })
})

describe('manifest.webmanifest', () => {
  const manifest = JSON.parse(manifestRaw) as {
    name: string
    short_name: string
    description: string
    start_url: string
    display: string
    theme_color: string
    background_color: string
    icons: { src: string; sizes: string; type: string; purpose?: string }[]
  }

  it('has the required installability fields', () => {
    expect(manifest.name).toBe('Library')
    expect(manifest.short_name).toBe('Library')
    expect(manifest.short_name.length).toBeLessThanOrEqual(12)
    expect(manifest.description).toBeTruthy()
    expect(manifest.start_url).toBe('/')
    // minimal-ui, not standalone: keeps Safari chrome on iOS, where
    // standalone-mode <input type=file> camera capture has a history of
    // quirks (rationale: docs/frontend.md §1.8.1).
    expect(manifest.display).toBe('minimal-ui')
    expect(manifest.theme_color).toMatch(/^#[0-9a-f]{6}$/)
    expect(manifest.background_color).toMatch(/^#[0-9a-f]{6}$/)
  })

  it('theme_color matches the theme-color meta (masthead colour)', () => {
    const meta = doc.querySelector('meta[name="theme-color"]')?.getAttribute('content')
    expect(meta).toBe(manifest.theme_color)
  })

  it('ships 192px, 512px and maskable icons that all exist', () => {
    const sizes = manifest.icons.map((icon) => icon.sizes)
    expect(sizes).toContain('192x192')
    expect(sizes).toContain('512x512')
    expect(manifest.icons.some((icon) => icon.purpose === 'maskable')).toBe(true)
    for (const icon of manifest.icons) {
      expect(icon.type).toBe('image/png')
      expect(existsSync(publicFile(icon.src)), `${icon.src} must exist in public/`).toBe(true)
    }
  })
})

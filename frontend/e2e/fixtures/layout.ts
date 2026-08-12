/**
 * Shared geometry helpers for the layout specs.
 *
 * These exist because layout is the one thing jsdom cannot check. A component
 * spec can mock `getBoundingClientRect` and assert whatever it likes; only a
 * real browser knows whether the composer actually sits at the bottom of the
 * viewport. So the rule this file serves is:
 *
 *   **Layout is asserted in Playwright, against real rects. jsdom specs assert
 *   behaviour and data flow only.**
 *
 * Lifted out of `responsive.spec.ts` when the second and third layout spec
 * needed the same two helpers — a fourth copy-pasted implementation is how the
 * assertions quietly drift apart.
 */
import { expect, type Page } from '@playwright/test'

/** A DOM rect as measured in the browser, plain enough to cross the bridge. */
export interface Rect {
  x: number
  y: number
  width: number
  height: number
  top: number
  right: number
  bottom: number
  left: number
}

/**
 * Fail when the document scrolls sideways.
 *
 * `+1` absorbs sub-pixel rounding: a layout that is correct can still report a
 * scrollWidth one larger than clientWidth on a fractional device pixel ratio,
 * and a spec that flakes on that gets deleted rather than fixed.
 */
export async function expectNoHorizontalOverflow(page: Page, label: string): Promise<void> {
  const { scrollWidth, clientWidth } = await page.evaluate(() => ({
    scrollWidth: document.documentElement.scrollWidth,
    clientWidth: document.documentElement.clientWidth,
  }))
  expect(
    scrollWidth,
    `${label}: scrollWidth ${scrollWidth} must not exceed clientWidth ${clientWidth} (+1 rounding)`,
  ).toBeLessThanOrEqual(clientWidth + 1)
}

/** Computed column count of the dashboard grid (0 when no grid is present). */
export async function gridColumnCount(page: Page): Promise<number> {
  const grid = page.locator('.app-doc-grid')
  if ((await grid.count()) === 0) return 0
  return grid.evaluate(
    (element) => getComputedStyle(element).gridTemplateColumns.split(' ').length,
  )
}

/**
 * The real rect of the first element matching `selector`.
 *
 * Deliberately `getBoundingClientRect` via `evaluate` rather than Playwright's
 * `boundingBox()`: `boundingBox()` returns null for an element that is not
 * visible, which turns "the element moved somewhere wrong" into a null-check
 * failure rather than a geometry failure with numbers in the message.
 */
export async function rectOf(page: Page, selector: string): Promise<Rect> {
  const rect = await page.locator(selector).first().evaluate((element) => {
    const r = element.getBoundingClientRect()
    return {
      x: r.x, y: r.y, width: r.width, height: r.height,
      top: r.top, right: r.right, bottom: r.bottom, left: r.left,
    }
  })
  return rect
}

/** The viewport as the page itself sees it (not the Playwright config value). */
export async function viewportOf(page: Page): Promise<{ width: number; height: number }> {
  return page.evaluate(() => ({
    width: document.documentElement.clientWidth,
    height: document.documentElement.clientHeight,
  }))
}

/**
 * Fail when `selector` is not docked within `tolerance` px of the viewport
 * bottom, or when it has been pushed off the top.
 *
 * Both halves matter and they fail differently: a composer that floats mid-page
 * after a short conversation is the `bf8da0c` defect, and one whose top has gone
 * negative is the `60a2f06` defect (docked, but taller than the space it was
 * given, so its head is cut off).
 */
export async function expectDockedToBottom(
  page: Page,
  selector: string,
  label: string,
  tolerance: number,
): Promise<void> {
  const rect = await rectOf(page, selector)
  const viewport = await viewportOf(page)
  const gap = viewport.height - rect.bottom
  expect(
    gap,
    `${label}: bottom edge is ${gap}px above the viewport bottom ` +
      `(max ${tolerance}px). Rect ${JSON.stringify(rect)}, viewport ${viewport.height}px.`,
  ).toBeLessThanOrEqual(tolerance)
  expect(
    rect.top,
    `${label}: top edge is ${rect.top}px — negative means it is cut off above the viewport`,
  ).toBeGreaterThanOrEqual(0)
}

/** Fail when two rects overlap vertically (a covers part of b, or vice versa). */
export function expectNoVerticalOverlap(a: Rect, b: Rect, label: string): void {
  const overlap = Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top)
  expect(
    overlap,
    `${label}: rects overlap by ${overlap}px — a=${JSON.stringify(a)} b=${JSON.stringify(b)}`,
  ).toBeLessThanOrEqual(0)
}

/**
 * Scroll the app's internal scroll container to the bottom.
 *
 * `window.scrollTo` does nothing in this app: the shell is a fixed-height flex
 * column and `#app-content` is the element that actually scrolls, so the
 * document's own scrollHeight always equals its clientHeight. A spec that
 * scrolls the window and then waits for something scroll-triggered will simply
 * time out with "element not found", which reads like a layout regression
 * rather than a spec bug — it cost an hour once, so it lives here now.
 */
export async function scrollAppContentToBottom(page: Page): Promise<void> {
  await page.evaluate(() => {
    const scroller = document.querySelector('#app-content')
    if (scroller) scroller.scrollTop = scroller.scrollHeight
  })
}

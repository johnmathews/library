import { describe, it, expect, beforeEach } from 'vitest'
import { mount, type VueWrapper } from '@vue/test-utils'
import SpendingLegend from '../SpendingLegend.vue'
import { OTHER_VALUE, OTHER_COLOUR, type Band } from '@/spending/palette'
import { SPLIT_PALETTE } from '@/utils/splitPalette'
import { formatMoney, fromCents } from '@/spending/money'

// Same three-band shape SpendingChart.spec.ts fixes: Hosting and Licences
// each earn their own band, Tools + Training fold into one "Other (2)" band.
// Built by hand rather than via the real `bands()` so the fixture stays a
// fixed, readable shape independent of palette.ts's fold threshold.
const BANDS: Band[] = [
  {
    value: 'hosting',
    label: 'Hosting',
    light: SPLIT_PALETTE[0]!.light,
    dark: SPLIT_PALETTE[0]!.dark,
    totalCents: 3300,
    members: [{ value: 'hosting', label: 'Hosting', colour: null }],
    isOther: false,
  },
  {
    value: 'licences',
    label: 'Licences',
    light: SPLIT_PALETTE[1]!.light,
    dark: SPLIT_PALETTE[1]!.dark,
    totalCents: 1800,
    members: [{ value: 'licences', label: 'Licences', colour: null }],
    isOther: false,
  },
  {
    value: OTHER_VALUE,
    label: 'Other (2)',
    light: OTHER_COLOUR.light,
    dark: OTHER_COLOUR.dark,
    totalCents: 1350,
    members: [
      { value: 'tools', label: 'Tools', colour: null },
      { value: 'training', label: 'Training', colour: null },
    ],
    isOther: true,
  },
]

function mountLegend(
  bandsOrHidden: Band[] | Set<string | null | symbol> = BANDS,
  hidden: Set<string | null | symbol> = new Set(),
): VueWrapper {
  const bands = Array.isArray(bandsOrHidden) ? bandsOrHidden : BANDS
  const hiddenSet = bandsOrHidden instanceof Set ? bandsOrHidden : hidden
  return mount(SpendingLegend, { props: { bands, hidden: hiddenSet, currency: 'USD' } })
}

function rowOf(wrapper: VueWrapper, label: string) {
  return wrapper
    .findAll('[data-testid="spending-legend-row"]')
    .find((row) => row.text().includes(label))!
}

// jsdom normalises an assigned hex colour to `rgb(r, g, b)` when it is read
// back off the element, so a raw hex literal never appears in `style`.
// Round-tripping the expected hex through the same normalisation is what
// makes the comparison exact rather than a same-format guess.
function cssColour(hex: string): string {
  const el = document.createElement('div')
  el.style.backgroundColor = hex
  return el.style.backgroundColor
}

describe('SpendingLegend', () => {
  beforeEach(() => {
    localStorage.clear()
    document.documentElement.classList.remove('dark')
    if (!window.matchMedia) {
      window.matchMedia = (() => ({
        matches: false,
        media: '',
        addEventListener() {},
        removeEventListener() {},
        addListener() {},
        removeListener() {},
        dispatchEvent() {
          return false
        },
      })) as unknown as typeof window.matchMedia
    }
  })

  it('renders a swatch, a label and a value for every band', () => {
    const wrapper = mountLegend()
    const rows = wrapper.findAll('[data-testid="spending-legend-row"]')
    expect(rows).toHaveLength(BANDS.length)

    const hosting = rowOf(wrapper, 'Hosting')
    expect(hosting.find('[data-testid="spending-legend-swatch"]').exists()).toBe(true)
    expect(hosting.text()).toContain('Hosting')
    expect(hosting.text()).toContain(formatMoney(fromCents(3300), 'USD'))

    const licences = rowOf(wrapper, 'Licences')
    expect(licences.text()).toContain(formatMoney(fromCents(1800), 'USD'))
  })

  // §4.7: an isolate that rewrote the headline would break §9.2's one promise,
  // in the direction that looks most plausible.
  it('emits isolate on click and exclude on modifier-click', async () => {
    const wrapper = mountLegend()
    await rowOf(wrapper, 'Hosting').trigger('click')
    expect(wrapper.emitted('isolate')![0]).toEqual(['hosting'])
    await rowOf(wrapper, 'Licences').trigger('click', { metaKey: true })
    expect(wrapper.emitted('exclude')![0]).toEqual(['licences'])
  })

  // ctrlKey must isolate/exclude just as metaKey does — Windows/Linux users
  // do not have a Cmd key. A modifier-click test that only exercised
  // metaKey would leave this branch unmutated and undetected.
  it('excludes on ctrl-click too, not only meta-click', async () => {
    const wrapper = mountLegend()
    await rowOf(wrapper, 'Licences').trigger('click', { ctrlKey: true })
    expect(wrapper.emitted('exclude')![0]).toEqual(['licences'])
    expect(wrapper.emitted('isolate')).toBeUndefined()
  })

  it('marks a hidden band as hidden without removing it from the legend', () => {
    // Removing it would leave no way to bring it back.
    const row = rowOf(mountLegend(new Set(['licences'])), 'Licences')
    expect(row.attributes('aria-pressed')).toBe('false')
    expect(row.exists()).toBe(true)
  })

  it('leaves a band that is not hidden marked as pressed/shown', () => {
    const row = rowOf(mountLegend(new Set(['licences'])), 'Hosting')
    expect(row.attributes('aria-pressed')).toBe('true')
  })

  it('names the folded values in the Other row so they are still identifiable', () => {
    // The Other row folds Tools and Training away — losing their names
    // would make the bucket unauditable, not just unlabelled.
    const wrapper = mountLegend()
    const other = rowOf(wrapper, 'Other (2)')
    expect(other.text()).toContain('Tools')
    expect(other.text()).toContain('Training')
  })

  it('renders nothing for an unsplit chart', () => {
    // One colour needs no legend; the chart's own name already says what is
    // plotted.
    expect(mountLegend([], new Set()).find('[data-testid="spending-legend"]').exists()).toBe(false)
  })

  // Extra coverage beyond the brief's pinned assertions ----------------------

  it('never derives a colour: the swatch is exactly band.light in light mode', () => {
    const wrapper = mountLegend()
    const swatch = rowOf(wrapper, 'Hosting').find('[data-testid="spending-legend-swatch"]')
    expect(swatch.attributes('style')).toContain(cssColour(SPLIT_PALETTE[0]!.light))
  })

  // useDark (`@vueuse/core`) resolves dark/light from the `vueuse-color-scheme`
  // localStorage key (falling back to matchMedia), not from a class already
  // sitting on <html> — so forcing the dark arm means seeding that key
  // before mount, the same primitive ThemeToggle.spec.ts exercises via the
  // checkbox. Mounted once per theme, per the coverage requirement that
  // Task 3 shipped without.
  it('renders the dark-theme swatch colour, distinct from the light one', () => {
    const lightWrapper = mountLegend()
    const lightHosting = rowOf(lightWrapper, 'Hosting')
      .find('[data-testid="spending-legend-swatch"]')
      .attributes('style')
    const lightLicences = rowOf(lightWrapper, 'Licences')
      .find('[data-testid="spending-legend-swatch"]')
      .attributes('style')

    localStorage.setItem('vueuse-color-scheme', 'dark')
    const darkWrapper = mountLegend()
    const darkHosting = rowOf(darkWrapper, 'Hosting')
      .find('[data-testid="spending-legend-swatch"]')
      .attributes('style')
    const darkLicences = rowOf(darkWrapper, 'Licences')
      .find('[data-testid="spending-legend-swatch"]')
      .attributes('style')

    // Both rendered bands change between themes, and each lands on ITS OWN
    // slot's dark hex — not just "different from light", which a bug that
    // shifted every band to the same wrong slot could still satisfy.
    expect(darkHosting).not.toBe(lightHosting)
    expect(darkHosting).toContain(cssColour(SPLIT_PALETTE[0]!.dark))
    expect(darkLicences).not.toBe(lightLicences)
    expect(darkLicences).toContain(cssColour(SPLIT_PALETTE[1]!.dark))
  })

  it('shows a reset control once a band is hidden, and clicking it emits reset', async () => {
    const wrapper = mountLegend(new Set(['licences']))
    const reset = wrapper.find('[data-testid="spending-legend-reset"]')
    expect(reset.exists()).toBe(true)
    await reset.trigger('click')
    expect(wrapper.emitted('reset')).toHaveLength(1)
  })

  it('has no reset control while nothing is hidden', () => {
    const wrapper = mountLegend()
    expect(wrapper.find('[data-testid="spending-legend-reset"]').exists()).toBe(false)
  })

  it('drops the Other row folded-member sub-line when compact, to save space', () => {
    const wrapper = mount(SpendingLegend, {
      props: { bands: BANDS, hidden: new Set<string | null | symbol>(), currency: 'USD', compact: true },
    })
    const other = rowOf(wrapper, 'Other (2)')
    expect(other.find('[data-testid="spending-legend-other-members"]').exists()).toBe(false)
    // Still names the band itself and its value — only the member list drops.
    expect(other.text()).toContain('Other (2)')
  })

  it('shows the Other row folded-member sub-line when not compact', () => {
    const wrapper = mountLegend()
    const other = rowOf(wrapper, 'Other (2)')
    expect(other.find('[data-testid="spending-legend-other-members"]').exists()).toBe(true)
  })

  it('renders and isolates a null-valued band (e.g. "no value for this facet")', async () => {
    const withNull: Band[] = [
      ...BANDS,
      {
        value: null,
        label: 'Unclassified',
        light: SPLIT_PALETTE[2]!.light,
        dark: SPLIT_PALETTE[2]!.dark,
        totalCents: 500,
        members: [{ value: null, label: 'Unclassified', colour: null }],
        isOther: false,
      },
    ]
    const wrapper = mount(SpendingLegend, {
      props: { bands: withNull, hidden: new Set<string | null | symbol>(), currency: 'USD' },
    })
    await rowOf(wrapper, 'Unclassified').trigger('click')
    expect(wrapper.emitted('isolate')![0]).toEqual([null])
  })
})

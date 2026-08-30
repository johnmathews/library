import { describe, expect, it } from 'vitest'
import { bands, OTHER_VALUE } from '../palette'
import { deriveSlot } from '@/utils/splitPalette'

const S = (value: string | null, label: string, colour: string | null = null) => ({ value, label, colour })
const C = (split_value: string | null, total: string) =>
  ({ split_value, total, period: '2026-01-01', payments: 1 })

describe('palette', () => {
  it('gives an unsplit chart no bands at all', () => {
    expect(bands([], [C(null, '10.00')])).toEqual([])
  })

  // The whole point of a hash-derived slot: the same key is the same colour
  // wherever it appears, including in 4c's vocabulary panel.
  it('gives a value the slot its key derives, when nothing collides', () => {
    const b = bands([S('hosting', 'Hosting')], [C('hosting', '10.00')])
    expect(b[0]!.light).toBe(deriveSlot('hosting').light)
  })

  // De-collision: two bands must never render the same colour.
  it('never gives two bands the same colour', () => {
    const keys = ['hosting', 'licences', 'tools', 'training', 'postage', 'freight']
    const b = bands(keys.map((k) => S(k, k)), keys.map((k) => C(k, '10.00')))
    const lights = b.map((x) => x.light)
    expect(new Set(lights).size).toBe(lights.length)
  })

  it('is deterministic under input reordering', () => {
    const keys = ['hosting', 'licences', 'tools', 'training']
    const cells = keys.map((k) => C(k, '10.00'))
    const one = bands(keys.map((k) => S(k, k)), cells).map((x) => [x.value, x.light])
    const two = bands([...keys].reverse().map((k) => S(k, k)), cells).map((x) => [x.value, x.light])
    expect(one).toEqual(two)
  })

  it('folds the tail past six into one Other bucket', () => {
    const keys = ['a', 'b', 'c', 'd', 'e', 'f', 'g', 'h']
    const cells = keys.map((k, i) => C(k, `${100 - i * 10}.00`))
    const b = bands(keys.map((k) => S(k, k.toUpperCase())), cells)
    expect(b.map((x) => (x.value === OTHER_VALUE ? 'OTHER' : x.value)))
      .toEqual(['a', 'b', 'c', 'd', 'e', 'f', 'OTHER'])
    expect(b.at(-1)!.label).toBe('Other (2)')
    expect(b.at(-1)!.members.map((m) => m.value)).toEqual(['g', 'h'])
  })

  // §9.2's promise: the stack height is the total, whatever the split does.
  it('preserves the total across the fold', () => {
    const keys = ['a', 'b', 'c', 'd', 'e', 'f', 'g', 'h']
    const cells = keys.map((k, i) => C(k, `${100 - i * 10}.11`))
    const b = bands(keys.map((k) => S(k, k)), cells)
    const expected = cells.reduce((n, c) => n + Math.round(parseFloat(c.total) * 100), 0)
    expect(b.reduce((n, x) => n + x.totalCents, 0)).toBe(expected)
  })

  // Forced tie: a fold that depends on input order is a fold that flickers.
  it('folds deterministically when every total is equal', () => {
    const keys = ['a', 'b', 'c', 'd', 'e', 'f', 'g']
    const cells = keys.map((k) => C(k, '10.00'))
    const names = (ks: string[]) =>
      bands(ks.map((k) => S(k, k)), cells).map((x) => (x.value === OTHER_VALUE ? 'OTHER' : x.value))
    expect(names(keys)).toEqual(names([...keys].reverse()))
    expect(names(keys)).toEqual(['a', 'b', 'c', 'd', 'e', 'f', 'OTHER'])
  })

  it('keeps the unlabelled bucket, sorted last, with the API label', () => {
    const b = bands([S(null, 'No category'), S('a', 'A')], [C(null, '5.00'), C('a', '9.00')])
    expect(b.map((x) => x.value)).toEqual(['a', null])
    expect(b[1]!.label).toBe('No category')
  })

  // A refund can exceed the payments in its bucket.
  it('ranks a negative net last without dropping it', () => {
    const b = bands([S('a', 'A'), S('b', 'B')], [C('a', '-5.00'), C('b', '20.00')])
    expect(b.map((x) => x.totalCents)).toEqual([-500, 2000])
  })

  // A stored colour claims its slot before any derived one is handed out.
  // `claimed` is deliberately `deriveSlot('b')`'s own slot: only a genuine
  // collision between 'a's override and 'b's derived pick exercises the
  // walk-forward de-collision, so this also proves pass 1 runs before pass 2
  // (a stored colour must reserve its slot before the derived pass hands it
  // to someone else).
  it('honours a stored colour and does not hand its slot out twice', () => {
    const claimed = deriveSlot('b')
    const b = bands([S('a', 'A', claimed.light), S('b', 'B')], [C('a', '1.00'), C('b', '2.00')])
    expect(b[0]!.light).toBe(claimed.light)
    expect(b[1]!.light).not.toBe(claimed.light)
  })

  it('keeps a split value that has no cells, as a real zero', () => {
    const b = bands([S('a', 'A'), S('b', 'B')], [C('a', '10.00')])
    expect(b.map((x) => [x.value, x.totalCents])).toEqual([['a', 1000], ['b', 0]])
  })
})

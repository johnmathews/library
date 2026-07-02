import { describe, it, expect, beforeEach } from 'vitest'
import { nextTick } from 'vue'
import {
  groupSeriesPoints,
  useChartsGrouping,
  CHARTS_GROUPING_STORAGE_KEY,
} from '../useChartsGrouping'

const points = [
  { date: '2025-01-05', amount: '100.00' },
  { date: '2025-01-20', amount: '50.00' },
  { date: '2025-02-10', amount: '30.00' },
  { date: '2025-04-01', amount: '200.00' },
]

describe('groupSeriesPoints', () => {
  it('sums amounts per month and keeps the count', () => {
    const out = groupSeriesPoints(points, 'month')
    expect(out.map(({ x, y, count }) => ({ x, y, count }))).toEqual([
      { x: '2025-01-01', y: 150, count: 2 },
      { x: '2025-02-01', y: 30, count: 1 },
      { x: '2025-04-01', y: 200, count: 1 },
    ])
  })

  it('carries a per-document breakdown (amount + label) for the tooltip', () => {
    const withTitles = [
      { date: '2025-01-05', amount: '100.00', label: 'Rent Jan 5' },
      { date: '2025-01-20', amount: '50.00' }, // no label -> falls back to date
    ]
    const [bucket] = groupSeriesPoints(withTitles, 'month')
    expect(bucket!.items).toEqual([
      { amount: 100, label: 'Rent Jan 5' },
      { amount: 50, label: '2025-01-20' },
    ])
  })

  it('sums amounts per quarter', () => {
    const out = groupSeriesPoints(points, 'quarter')
    expect(out.map(({ x, y, count }) => ({ x, y, count }))).toEqual([
      { x: '2025-01-01', y: 180, count: 3 },
      { x: '2025-04-01', y: 200, count: 1 },
    ])
  })

  it('sums amounts per year', () => {
    const [bucket] = groupSeriesPoints(points, 'year')
    expect({ x: bucket!.x, y: bucket!.y, count: bucket!.count }).toEqual({
      x: '2025-01-01',
      y: 380,
      count: 4,
    })
    expect(bucket!.items).toHaveLength(4)
  })

  it('buckets by ISO week (Monday start)', () => {
    const out = groupSeriesPoints(
      [
        { date: '2025-01-06', amount: '10' }, // Monday
        { date: '2025-01-12', amount: '5' }, // Sunday, same ISO week
        { date: '2025-01-13', amount: '7' }, // next Monday
      ],
      'week',
    )
    expect(out.map(({ x, y, count }) => ({ x, y, count }))).toEqual([
      { x: '2025-01-06', y: 15, count: 2 },
      { x: '2025-01-13', y: 7, count: 1 },
    ])
  })

  it('treats non-numeric amounts as zero and is safe on empty input', () => {
    expect(groupSeriesPoints([], 'month')).toEqual([])
    expect(groupSeriesPoints([{ date: '2025-01-01', amount: 'n/a' }], 'month')).toEqual([
      { x: '2025-01-01', y: 0, count: 1, items: [{ amount: 0, label: '2025-01-01' }] },
    ])
  })
})

describe('useChartsGrouping', () => {
  beforeEach(() => localStorage.clear())

  it('defaults to monthly grouping and persists a choice', async () => {
    const { grouping } = useChartsGrouping()
    expect(grouping.value).toBe('month')
    grouping.value = 'quarter'
    await nextTick() // let useStorage flush to localStorage
    expect(localStorage.getItem(CHARTS_GROUPING_STORAGE_KEY)).toContain('quarter')
    expect(useChartsGrouping().grouping.value).toBe('quarter')
  })
})

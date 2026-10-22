import { describe, expect, it } from 'vitest'
import { backendLabel, modelLabel } from '../modelLabel'

describe('modelLabel', () => {
  it.each([
    ['claude-opus-4-8', 'Opus 4.8'],
    ['claude-sonnet-4-6', 'Sonnet 4.6'],
    ['claude-haiku-4-5-20251001', 'Haiku 4.5'],
    ['claude-opus-5', 'Opus 5'],
  ])('names %s as %s', (id, label) => {
    expect(modelLabel(id)).toBe(label)
  })

  it('shows an unfamiliar id as-is rather than guessing', () => {
    expect(modelLabel('gpt-something')).toBe('gpt-something')
    expect(modelLabel('claude-')).toBe('claude-')
  })
})

describe('backendLabel', () => {
  it('names both backends', () => {
    expect(backendLabel('subscription')).toBe('Subscription')
    expect(backendLabel('api')).toBe('API')
  })
})

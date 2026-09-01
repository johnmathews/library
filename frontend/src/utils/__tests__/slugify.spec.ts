import { describe, expect, it } from 'vitest'
import { slugify } from '../slugify'

describe('slugify', () => {
  it('lowercases and hyphenates spaces', () => {
    expect(slugify('EV charging (home)!')).toBe('ev-charging-home')
  })

  it('strips characters outside [a-z0-9_-]', () => {
    expect(slugify('Café & Bar!!')).toBe('caf-bar')
  })

  it('collapses runs of the same separator but not mixed adjacent ones', () => {
    expect(slugify('one -- two __ three')).toBe('one-two-_-three')
  })

  it('trims leading and trailing separators', () => {
    expect(slugify('  -_hello world_-  ')).toBe('hello-world')
  })

  it('truncates to 64 characters and re-trims a trailing separator left by the cut', () => {
    const label = 'a'.repeat(63) + ' b'
    const result = slugify(label)
    expect(result.length).toBeLessThanOrEqual(64)
    expect(result).toBe('a'.repeat(63))
  })

  it('returns an empty string for a label with nothing sluggable', () => {
    expect(slugify('!!!')).toBe('')
  })

  it('preserves underscores and hyphens already present', () => {
    expect(slugify('already_a-key')).toBe('already_a-key')
  })
})

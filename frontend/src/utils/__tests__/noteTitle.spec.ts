import { describe, expect, it } from 'vitest'
import { deriveNoteTitle } from '../noteTitle'

describe('deriveNoteTitle', () => {
  it('uses the first line of the body', () => {
    expect(deriveNoteTitle('Groceries\nmilk\neggs')).toBe('Groceries')
  })

  it('strips a leading markdown heading marker', () => {
    expect(deriveNoteTitle('# Groceries\nmilk')).toBe('Groceries')
    expect(deriveNoteTitle('### Weekly plan')).toBe('Weekly plan')
  })

  it('skips leading blank lines to the first content line', () => {
    expect(deriveNoteTitle('\n\n   \nReal title\nbody')).toBe('Real title')
  })

  it('trims surrounding whitespace', () => {
    expect(deriveNoteTitle('   Padded title   \nbody')).toBe('Padded title')
  })

  it('returns an empty string for an empty or whitespace-only body', () => {
    expect(deriveNoteTitle('')).toBe('')
    expect(deriveNoteTitle('   \n\t\n  ')).toBe('')
  })

  it('does not treat a bare # with no text as a heading marker', () => {
    // No space after the hashes → not a heading; keep the line verbatim.
    expect(deriveNoteTitle('#tag line')).toBe('#tag line')
  })

  it('caps the title at 200 characters', () => {
    const long = 'a'.repeat(250)
    expect(deriveNoteTitle(long)).toHaveLength(200)
  })
})

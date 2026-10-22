import { describe, expect, it } from 'vitest'
import { renderAnswer } from '../renderAnswer'

function dom(html: string): HTMLElement {
  const div = document.createElement('div')
  div.innerHTML = html
  return div
}

// Wide GFM tables used to pan the whole transcript sideways: the transcript is
// overflow-y-auto, which makes the browser compute overflow-x to auto too.
// Each table gets its own scroll container instead. These pin the DOM contract
// the CSS hangs off; the geometry is asserted in e2e, where there is layout.
const TABLE =
  '| Disk | Serial | Standby % | Active % | Cycles | Baseline | Status |\n' +
  '| --- | --- | --- | --- | --- | --- | --- |\n' +
  '| sdf | K3S04BKQ | 1.0% | 99.0% | 2 | 3.1 | Normal |'

describe('renderAnswer', () => {
  it('renders markdown to HTML', () => {
    const el = dom(renderAnswer('Your supplier was **PWN** [#67].\n\n- one\n- two'))
    expect(el.querySelector('strong')?.textContent).toBe('PWN')
    expect(el.innerHTML).not.toContain('**PWN**')
    expect(el.querySelectorAll('li')).toHaveLength(2)
  })

  it('wraps a table in its own scroll container, with the table inside it', () => {
    const el = dom(renderAnswer(TABLE))
    const wrap = el.querySelector('.ask-table-wrap')
    expect(wrap?.querySelector('table')).not.toBeNull()
    expect(el.querySelectorAll('table')).toHaveLength(1)
  })

  it('keeps the wrapper keyboard-reachable through DOMPurify', () => {
    // tabindex is not in DOMPurify's default allow-list; without ADD_ATTR the
    // scrollable region silently becomes unreachable by keyboard.
    const wrap = dom(renderAnswer(TABLE)).querySelector('.ask-table-wrap')!
    expect(wrap.getAttribute('tabindex')).toBe('0')
    expect(wrap.getAttribute('role')).toBe('region')
    expect(wrap.getAttribute('aria-label')).toBe('Table')
  })

  it('wraps every table when there is more than one', () => {
    const el = dom(renderAnswer(`${TABLE}\n\nAnd another:\n\n${TABLE}`))
    expect(el.querySelectorAll('.ask-table-wrap')).toHaveLength(2)
    expect(el.querySelectorAll('.ask-table-wrap table')).toHaveLength(2)
  })

  it('leaves an answer with no table unwrapped', () => {
    expect(dom(renderAnswer('Just prose.')).querySelector('.ask-table-wrap')).toBeNull()
  })

  it('strips script the model echoed from a document', () => {
    const el = dom(renderAnswer('Hello <img src=x onerror="alert(1)"><script>alert(2)</script>'))
    expect(el.querySelector('script')).toBeNull()
    expect(el.querySelector('img')?.getAttribute('onerror')).toBeNull()
  })
})

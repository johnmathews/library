import { describe, expect, it } from 'vitest'
import { escapeHtml, renderSnippet } from '../snippet'

describe('escapeHtml', () => {
  it('escapes all five HTML-special characters', () => {
    expect(escapeHtml(`<a href="x" onclick='y'>&</a>`)).toBe(
      '&lt;a href=&quot;x&quot; onclick=&#39;y&#39;&gt;&amp;&lt;/a&gt;',
    )
  })
})

describe('renderSnippet', () => {
  it('keeps the ts_headline <b> markers as real elements', () => {
    expect(renderSnippet('uw <b>rekening</b> voor mei … totaal')).toBe(
      'uw <b>rekening</b> voor mei … totaal',
    )
  })

  it('neutralises script tags in OCR text', () => {
    const html = renderSnippet('foo <script>alert(1)</script> <b>bar</b>')
    expect(html).toBe('foo &lt;script&gt;alert(1)&lt;/script&gt; <b>bar</b>')
    expect(html).not.toContain('<script')
  })

  it('neutralises event-handler injection', () => {
    const html = renderSnippet('<img src=x onerror=alert(1)> and <b>match</b>')
    expect(html).toBe('&lt;img src=x onerror=alert(1)&gt; and <b>match</b>')
    expect(html).not.toContain('<img')
  })

  it('does not let attributes smuggle through bold markers', () => {
    // Only the exact `<b>` / `</b>` sequences are converted back.
    expect(renderSnippet('<b onmouseover=alert(1)>x</b>')).toBe(
      '&lt;b onmouseover=alert(1)&gt;x</b>',
    )
  })

  it('escapes quotes and ampersands', () => {
    expect(renderSnippet(`"Tom & Jerry's" <b>invoice</b>`)).toBe(
      '&quot;Tom &amp; Jerry&#39;s&quot; <b>invoice</b>',
    )
  })

  it('produces no executable content when injected into the DOM', () => {
    const container = document.createElement('div')
    container.innerHTML = renderSnippet('<script>window.__pwned = true</script><b>q</b>')
    expect(container.querySelector('script')).toBeNull()
    expect(container.querySelectorAll('b')).toHaveLength(1)
    expect((window as unknown as Record<string, unknown>).__pwned).toBeUndefined()
  })
})

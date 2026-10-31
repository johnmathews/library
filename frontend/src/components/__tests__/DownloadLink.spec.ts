import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'

import DownloadLink from '../DownloadLink.vue'
import { filenameFromDisposition } from '@/utils/contentDisposition'

const HREF = '/api/documents/7/original'

/** Pretend to be an iOS home-screen web app (`navigator.standalone`). */
function setStandalone(value: boolean | undefined): void {
  Object.defineProperty(window.navigator, 'standalone', { value, configurable: true })
}

function setShare(share: unknown, canShare: unknown = vi.fn(() => true)): void {
  Object.defineProperty(window.navigator, 'share', { value: share, configurable: true })
  Object.defineProperty(window.navigator, 'canShare', { value: canShare, configurable: true })
}

/** A minimal fetch Response: jsdom's Blob cannot back Node's real Response. */
function okResponse(disposition: string, type = 'application/pdf'): Response {
  return {
    ok: true,
    headers: new Headers({ 'Content-Type': type, 'Content-Disposition': disposition }),
    blob: async () => new Blob(['%PDF-1.7'], { type }),
  } as Response
}

let assign: ReturnType<typeof vi.fn>

beforeEach(() => {
  assign = vi.fn()
  vi.spyOn(window, 'location', 'get').mockReturnValue({ ...window.location, assign } as Location)
})

afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
  setStandalone(undefined)
  setShare(undefined, undefined)
  document.body.innerHTML = ''
})

function mountLink() {
  return mount(DownloadLink, {
    props: { href: HREF },
    slots: { default: 'Download' },
    attachTo: document.body,
  })
}

/** Click the link and report whether the browser's own navigation survived. */
async function click(wrapper: ReturnType<typeof mountLink>): Promise<boolean> {
  // Read the outcome once the link's own handler has run, then stop jsdom
  // attempting the navigation (it only logs "Not implemented").
  let browserDefault = false
  const record = (event: Event): void => {
    browserDefault = !event.defaultPrevented
    event.preventDefault()
  }
  document.addEventListener('click', record, { once: true })
  wrapper.element.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }))
  await flushPromises()
  return browserDefault
}

describe('filenameFromDisposition', () => {
  it('reads a quoted ASCII filename', () => {
    expect(filenameFromDisposition('attachment; filename="bill.pdf"', 'x')).toBe('bill.pdf')
  })

  it('prefers the RFC 5987 filename* form and decodes it', () => {
    expect(
      filenameFromDisposition(`attachment; filename*=utf-8''r%C3%A9sum%C3%A9%20v2.pdf`, 'x'),
    ).toBe('résumé v2.pdf')
  })

  it('falls back when the header is missing or has no filename', () => {
    expect(filenameFromDisposition(null, 'document')).toBe('document')
    expect(filenameFromDisposition('attachment', 'document')).toBe('document')
  })
})

describe('DownloadLink', () => {
  it('renders a plain attachment link with fallthrough attrs and slot content', () => {
    const wrapper = mount(DownloadLink, {
      attachTo: document.body,
      props: { href: HREF },
      attrs: { 'data-testid': 'download-original', class: 'btn-sm' },
      slots: { default: 'Download original' },
    })
    expect(wrapper.element.tagName).toBe('A')
    expect(wrapper.attributes('href')).toBe(HREF)
    expect(wrapper.attributes('data-testid')).toBe('download-original')
    expect(wrapper.classes()).toContain('btn-sm')
    expect(wrapper.text()).toBe('Download original')
  })

  it('leaves the click to the browser outside an iOS home-screen app', async () => {
    const fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)
    setShare(vi.fn())
    const wrapper = mountLink()
    expect(await click(wrapper)).toBe(true)
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('leaves the click to the browser in standalone mode without the Web Share API', async () => {
    const fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)
    setStandalone(true)
    const wrapper = mountLink()
    expect(await click(wrapper)).toBe(true)
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('in an iOS home-screen app, hands the named file to the share sheet', async () => {
    setStandalone(true)
    const share = vi.fn(async () => {})
    setShare(share)
    vi.stubGlobal('fetch', vi.fn(async () => okResponse('attachment; filename="bill.pdf"')))
    const wrapper = mountLink()

    expect(await click(wrapper)).toBe(false)
    expect(fetch).toHaveBeenCalledWith(HREF, { credentials: 'same-origin' })
    expect(share).toHaveBeenCalledTimes(1)
    const [{ files }] = share.mock.calls[0] as unknown as [{ files: File[] }]
    expect(files).toHaveLength(1)
    expect(files[0]!.name).toBe('bill.pdf')
    expect(files[0]!.type).toBe('application/pdf')
    expect(assign).not.toHaveBeenCalled()
    expect(wrapper.text()).toBe('Download')
  })

  it('shows "Preparing…" while the file is fetched', async () => {
    setStandalone(true)
    setShare(vi.fn(async () => {}))
    let resolve!: (r: Response) => void
    vi.stubGlobal('fetch', vi.fn(() => new Promise<Response>((r) => (resolve = r))))
    const wrapper = mountLink()

    await click(wrapper)
    expect(wrapper.text()).toBe('Preparing…')
    expect(wrapper.attributes('aria-busy')).toBe('true')
    resolve(okResponse('attachment; filename="a.pdf"'))
    await flushPromises()
    expect(wrapper.text()).toBe('Download')
  })

  it('when iOS rejects the late share, the next tap shares the prepared file without refetching', async () => {
    setStandalone(true)
    const share = vi
      .fn()
      .mockRejectedValueOnce(new DOMException('no activation', 'NotAllowedError'))
      .mockResolvedValueOnce(undefined)
    setShare(share)
    vi.stubGlobal('fetch', vi.fn(async () => okResponse('attachment; filename="bill.pdf"')))
    const wrapper = mountLink()

    await click(wrapper)
    expect(wrapper.text()).toBe('Tap again to save')

    expect(await click(wrapper)).toBe(false)
    expect(fetch).toHaveBeenCalledTimes(1)
    expect(share).toHaveBeenCalledTimes(2)
    expect((share.mock.calls[1] as unknown as [{ files: File[] }])[0].files[0]!.name).toBe('bill.pdf')
    expect(wrapper.text()).toBe('Download')
  })

  it('treats a dismissed share sheet as done, not as a failure', async () => {
    setStandalone(true)
    setShare(vi.fn().mockRejectedValue(new DOMException('cancelled', 'AbortError')))
    vi.stubGlobal('fetch', vi.fn(async () => okResponse('attachment; filename="bill.pdf"')))
    const wrapper = mountLink()

    await click(wrapper)
    expect(wrapper.text()).toBe('Download')
    expect(assign).not.toHaveBeenCalled()
  })

  it('falls back to navigating to the file when the share itself errors', async () => {
    setStandalone(true)
    setShare(vi.fn().mockRejectedValue(new TypeError('files unsupported')))
    vi.stubGlobal('fetch', vi.fn(async () => okResponse('attachment; filename="bill.pdf"')))
    const wrapper = mountLink()

    await click(wrapper)
    expect(assign).toHaveBeenCalledWith(HREF)
    expect(wrapper.text()).toBe('Download')
  })

  it('falls back to navigating to the file when it cannot be shared', async () => {
    setStandalone(true)
    setShare(vi.fn(), vi.fn(() => false))
    vi.stubGlobal('fetch', vi.fn(async () => okResponse('attachment; filename="bill.pdf"')))
    const wrapper = mountLink()

    await click(wrapper)
    expect(navigator.share).not.toHaveBeenCalled()
    expect(assign).toHaveBeenCalledWith(HREF)
  })

  it('falls back to navigating to the file when the fetch fails', async () => {
    setStandalone(true)
    setShare(vi.fn())
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: false, status: 404 }) as Response))
    const wrapper = mountLink()

    await click(wrapper)
    expect(navigator.share).not.toHaveBeenCalled()
    expect(assign).toHaveBeenCalledWith(HREF)
    expect(wrapper.text()).toBe('Download')
  })

  it('falls back to navigating to the file when the network fails', async () => {
    setStandalone(true)
    setShare(vi.fn())
    vi.stubGlobal('fetch', vi.fn(async () => Promise.reject(new TypeError('offline'))))
    const wrapper = mountLink()

    await click(wrapper)
    expect(assign).toHaveBeenCalledWith(HREF)
  })

  it('drops a prepared file when the link moves to another document', async () => {
    // The detail view is reused across previous/next navigation, so only `href`
    // changes: document A's file must never be shared from document B's page.
    setStandalone(true)
    const share = vi
      .fn()
      .mockRejectedValueOnce(new DOMException('no activation', 'NotAllowedError'))
      .mockResolvedValue(undefined)
    setShare(share)
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string) =>
        okResponse(`attachment; filename="${url.includes('/7/') ? 'a.pdf' : 'b.pdf'}"`),
      ),
    )
    const wrapper = mountLink()
    await click(wrapper)
    expect(wrapper.text()).toBe('Tap again to save')

    await wrapper.setProps({ href: '/api/documents/8/original' })
    expect(wrapper.text()).toBe('Download')
    await click(wrapper)
    expect(fetch).toHaveBeenLastCalledWith('/api/documents/8/original', { credentials: 'same-origin' })
    expect((share.mock.calls[1] as unknown as [{ files: File[] }])[0].files[0]!.name).toBe('b.pdf')
  })

  it('discards a fetch that finishes after the link moved to another document', async () => {
    setStandalone(true)
    const share = vi.fn(async () => {})
    setShare(share)
    let resolve!: (r: Response) => void
    vi.stubGlobal('fetch', vi.fn(() => new Promise<Response>((r) => (resolve = r))))
    const wrapper = mountLink()
    await click(wrapper)

    await wrapper.setProps({ href: '/api/documents/8/original' })
    expect(wrapper.text()).toBe('Download')
    resolve(okResponse('attachment; filename="a.pdf"'))
    await flushPromises()
    expect(share).not.toHaveBeenCalled()
    expect(assign).not.toHaveBeenCalled()
    expect(wrapper.text()).toBe('Download')
  })

  it('leaves modifier and non-primary clicks to the browser', async () => {
    setStandalone(true)
    setShare(vi.fn())
    const fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)
    const wrapper = mountLink()
    for (const init of [{ metaKey: true }, { ctrlKey: true }, { shiftKey: true }, { altKey: true }, { button: 1 }]) {
      let browserDefault = false
      document.addEventListener(
        'click',
        (e) => {
          browserDefault = !e.defaultPrevented
          e.preventDefault()
        },
        { once: true },
      )
      wrapper.element.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, ...init }))
      await flushPromises()
      expect(browserDefault).toBe(true)
    }
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('ignores taps while a file is already being prepared', async () => {
    setStandalone(true)
    setShare(vi.fn(async () => {}))
    let resolve!: (r: Response) => void
    vi.stubGlobal('fetch', vi.fn(() => new Promise<Response>((r) => (resolve = r))))
    const wrapper = mountLink()

    await click(wrapper)
    expect(await click(wrapper)).toBe(false)
    expect(fetch).toHaveBeenCalledTimes(1)
    resolve(okResponse('attachment; filename="a.pdf"'))
    await flushPromises()
  })
})

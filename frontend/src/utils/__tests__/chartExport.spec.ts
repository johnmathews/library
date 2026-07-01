import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'

// Capture jsPDF usage. Hoisted so the vi.mock factory (also hoisted) can see it.
const { pdfInstance, jsPDFCtor } = vi.hoisted(() => {
  const pdfInstance = {
    internal: { pageSize: { getWidth: () => 595 } },
    setFontSize: vi.fn(),
    text: vi.fn(),
    addImage: vi.fn(),
    save: vi.fn(),
  }
  // A regular function (not an arrow) so it can be used with `new`.
  return {
    pdfInstance,
    jsPDFCtor: vi.fn(function () {
      return pdfInstance
    }),
  }
})
vi.mock('jspdf', () => ({ jsPDF: jsPDFCtor }))

import { slugifyFilename, downloadImage, downloadPdf, copyShareUrl } from '../chartExport'

// A canvas the export code can rasterise. `document.createElement('canvas')`
// (the white-composite target) also returns one of these via the spy below.
function fakeCanvas(width = 800, height = 400): HTMLCanvasElement {
  return {
    width,
    height,
    getContext: () => ({ fillStyle: '', fillRect: vi.fn(), drawImage: vi.fn() }),
    toDataURL: (mime: string) => `data:${mime};base64,AAAA`,
  } as unknown as HTMLCanvasElement
}

const realCreateElement = document.createElement.bind(document)
let clickSpy: ReturnType<typeof vi.spyOn>

beforeEach(() => {
  vi.clearAllMocks()
  vi.spyOn(document, 'createElement').mockImplementation((tag: string) => {
    if (tag === 'canvas') return fakeCanvas()
    return realCreateElement(tag)
  })
  clickSpy = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
})

afterEach(() => vi.restoreAllMocks())

describe('slugifyFilename', () => {
  it('lowercases, strips punctuation, collapses separators', () => {
    expect(slugifyFilename('De Hooge Waerder · irregular series')).toBe(
      'de-hooge-waerder-irregular-series',
    )
  })
  it('falls back to "chart" for an empty name', () => {
    expect(slugifyFilename('   ')).toBe('chart')
  })
})

describe('downloadImage', () => {
  it('downloads a JPEG named after the series', () => {
    downloadImage(fakeCanvas(), 'jpeg', 'My Series')
    expect(clickSpy).toHaveBeenCalledOnce()
    const anchor = clickSpy.mock.instances[0] as unknown as HTMLAnchorElement
    expect(anchor.download).toBe('my-series.jpg')
  })

  it('downloads a PNG (transparent, no composite) with a .png extension', () => {
    downloadImage(fakeCanvas(), 'png', 'My Series')
    const anchor = clickSpy.mock.instances[0] as unknown as HTMLAnchorElement
    expect(anchor.download).toBe('my-series.png')
  })
})

describe('downloadPdf', () => {
  it('builds a one-page PDF with a title and the chart image, then saves', () => {
    downloadPdf(fakeCanvas(800, 400), 'My Series', 'My Series')
    // Landscape because width >= height.
    expect(jsPDFCtor).toHaveBeenCalledWith(
      expect.objectContaining({ orientation: 'landscape', format: 'a4' }),
    )
    expect(pdfInstance.text).toHaveBeenCalledWith('My Series', 40, 40)
    expect(pdfInstance.addImage).toHaveBeenCalled()
    expect(pdfInstance.save).toHaveBeenCalledWith('my-series.pdf')
  })
})

describe('copyShareUrl', () => {
  it('writes the given URL to the clipboard', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    vi.stubGlobal('navigator', { clipboard: { writeText } })
    await copyShareUrl('https://example.test/charts/a-1')
    expect(writeText).toHaveBeenCalledWith('https://example.test/charts/a-1')
    vi.unstubAllGlobals()
  })
})

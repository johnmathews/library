import { jsPDF } from 'jspdf'

/**
 * Client-side chart export/share (W6). The chart is a Chart.js `<canvas>`; the
 * single-chart page hands us that canvas (via the tile's `getChartCanvas`) and
 * we rasterise it to a JPEG/PNG download or embed it in a one-page PDF. Sharing
 * is just copying the deep-link URL. All of it runs in the browser — no server
 * round-trip and no backend changes.
 */
export type ImageFormat = 'jpeg' | 'png'

/** A filesystem-safe slug for a series name (falls back to "chart"). */
export function slugifyFilename(name: string): string {
  const slug = name
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
  return slug || 'chart'
}

function triggerDownload(dataUrl: string, filename: string): void {
  const a = document.createElement('a')
  a.href = dataUrl
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
}

/**
 * Flatten the (transparent) chart canvas onto a white background. Chart.js
 * renders on transparency, which becomes black in JPEG/PDF; compositing keeps
 * the export readable on any viewer.
 */
function onWhite(canvas: HTMLCanvasElement): HTMLCanvasElement {
  const out = document.createElement('canvas')
  out.width = canvas.width
  out.height = canvas.height
  const ctx = out.getContext('2d')
  if (ctx) {
    ctx.fillStyle = '#ffffff'
    ctx.fillRect(0, 0, out.width, out.height)
    ctx.drawImage(canvas, 0, 0)
  }
  return out
}

/** Download the chart as a JPEG or PNG named after the series. */
export function downloadImage(canvas: HTMLCanvasElement, format: ImageFormat, name: string): void {
  const ext = format === 'png' ? 'png' : 'jpg'
  // PNG keeps transparency; JPEG needs the white composite.
  const source = format === 'png' ? canvas : onWhite(canvas)
  const dataUrl = source.toDataURL(format === 'png' ? 'image/png' : 'image/jpeg', 0.92)
  triggerDownload(dataUrl, `${slugifyFilename(name)}.${ext}`)
}

/** Download the chart as a one-page PDF with the series name as a heading. */
export function downloadPdf(canvas: HTMLCanvasElement, title: string, name: string): void {
  const img = onWhite(canvas)
  const imgData = img.toDataURL('image/jpeg', 0.92)
  const orientation: 'landscape' | 'portrait' = img.width >= img.height ? 'landscape' : 'portrait'
  const pdf = new jsPDF({ orientation, unit: 'pt', format: 'a4' })

  const margin = 40
  const pageWidth = pdf.internal.pageSize.getWidth()
  const availWidth = pageWidth - margin * 2
  const ratio = img.width > 0 ? img.height / img.width : 0.5

  pdf.setFontSize(14)
  pdf.text(title, margin, margin)
  pdf.addImage(imgData, 'JPEG', margin, margin + 16, availWidth, availWidth * ratio)
  pdf.save(`${slugifyFilename(name)}.pdf`)
}

/** Copy a shareable URL (the current deep link by default) to the clipboard. */
export function copyShareUrl(url: string = window.location.href): Promise<void> {
  return navigator.clipboard.writeText(url)
}

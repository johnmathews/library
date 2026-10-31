/**
 * The filename a `Content-Disposition` header names, or `fallback`.
 *
 * Starlette's `FileResponse` sends `filename="…"` for ASCII names and the
 * RFC 5987 `filename*=utf-8''…` form otherwise; the extended form wins when
 * both are present, as RFC 6266 §4.3 requires.
 */
export function filenameFromDisposition(header: string | null, fallback: string): string {
  if (!header) return fallback
  const extended = /filename\*\s*=\s*[^']*'[^']*'([^;]+)/i.exec(header)
  if (extended) {
    try {
      return decodeURIComponent(extended[1]!.trim())
    } catch {
      // Malformed percent-encoding: fall through to the plain form.
    }
  }
  const plain = /filename\s*=\s*"([^"]*)"|filename\s*=\s*([^;]+)/i.exec(header)
  const name = (plain?.[1] ?? plain?.[2])?.trim()
  return name || fallback
}

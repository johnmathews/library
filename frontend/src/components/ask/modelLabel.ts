/**
 * Human labels for the Ask composer's "model · backend" chip.
 *
 * Model ids follow `claude-<family>-<major>-<minor>[-<date>]`, e.g.
 * `claude-opus-4-8` → "Opus 4.8" and `claude-haiku-4-5-20251001` → "Haiku 4.5".
 * Anything else is shown as-is: an unfamiliar id is still true, and a guessed
 * prettification of it would not be.
 */
const MODEL_ID = /^claude-([a-z]+)-(\d+)(?:-(\d{1,2}))?(?:-\d{8})?$/

export function modelLabel(model: string): string {
  const match = MODEL_ID.exec(model)
  const [, family, major, minor] = match ?? []
  if (!family || !major) return model
  const name = family.charAt(0).toUpperCase() + family.slice(1)
  return minor === undefined ? `${name} ${major}` : `${name} ${major}.${minor}`
}

export function backendLabel(backend: 'api' | 'subscription'): string {
  return backend === 'subscription' ? 'Subscription' : 'API'
}

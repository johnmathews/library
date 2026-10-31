<script setup lang="ts">
/**
 * A file-download link that also works in an iOS home-screen web app.
 *
 * Everywhere else it is a plain `<a :href>` to an attachment URL and the
 * browser downloads the file. An iOS home-screen app (`navigator.standalone`)
 * has no download manager: following the link opens a file view with no save
 * or share control (WebKit bug 290847). There, a tap fetches the file and
 * hands it to the Web Share API, whose share sheet offers "Save to Files".
 *
 * iOS only lets `navigator.share` run close to a tap. A slow fetch can outlive
 * that window and the share is refused with `NotAllowedError`; the prepared
 * file is then kept and the label asks for a second tap, which shares it
 * straight away. A dismissed sheet does nothing; anything else that goes wrong
 * (the fetch fails, `canShare` refuses the file, the share errors) falls back
 * to following the link — never worse than before.
 *
 * The document view is reused across previous/next navigation, so only `href`
 * changes between documents: a change drops any prepared file and orphans an
 * in-flight fetch, or one document's file would be shared from another's page.
 */
defineOptions({ name: 'DownloadLink' })

import { ref, watch } from 'vue'
import { filenameFromDisposition } from '@/utils/contentDisposition'

const props = defineProps<{ href: string }>()

type State = 'idle' | 'preparing' | 'ready'
const state = ref<State>('idle')
let prepared: File | null = null
// Bumped on every href change; a fetch whose generation is stale is discarded.
let generation = 0

watch(
  () => props.href,
  () => {
    generation++
    prepared = null
    state.value = 'idle'
  },
)

function isIosHomeScreenApp(): boolean {
  return (navigator as Navigator & { standalone?: boolean }).standalone === true
}

function canShareFiles(): boolean {
  return typeof navigator.share === 'function' && typeof navigator.canShare === 'function'
}

async function fetchFile(href: string): Promise<File | null> {
  try {
    const response = await fetch(href, { credentials: 'same-origin' })
    if (!response.ok) return null
    const blob = await response.blob()
    const name = filenameFromDisposition(response.headers.get('Content-Disposition'), 'document')
    return new File([blob], name, { type: blob.type })
  } catch {
    return null
  }
}

async function share(file: File, href: string): Promise<void> {
  try {
    await navigator.share({ files: [file] })
    state.value = 'idle'
    prepared = null
  } catch (err) {
    const name = err instanceof DOMException ? err.name : ''
    if (name === 'NotAllowedError') {
      prepared = file
      state.value = 'ready'
      return
    }
    state.value = 'idle'
    prepared = null
    // AbortError: the user dismissed the sheet. Anything else is a share the
    // platform could not do, so fall back to the plain link.
    if (name !== 'AbortError') window.location.assign(href)
  }
}

async function onClick(event: MouseEvent): Promise<void> {
  if (!isIosHomeScreenApp() || !canShareFiles()) return
  // Modified and non-primary clicks keep their native meaning.
  if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return
  event.preventDefault()
  if (state.value === 'preparing') return
  if (state.value === 'ready' && prepared) {
    await share(prepared, props.href)
    return
  }
  state.value = 'preparing'
  const href = props.href
  const started = generation
  const file = await fetchFile(href)
  if (started !== generation) return
  if (!file || !navigator.canShare({ files: [file] })) {
    state.value = 'idle'
    window.location.assign(href)
    return
  }
  await share(file, href)
}
</script>

<template>
  <a :href="href" :aria-busy="state === 'preparing' ? 'true' : undefined" @click="onClick">
    <template v-if="state === 'preparing'">Preparing…</template>
    <template v-else-if="state === 'ready'">Tap again to save</template>
    <slot v-else />
  </a>
</template>

import { readonly, ref, type DeepReadonly, type Ref } from 'vue'

/**
 * The current page's title, lifted out of the page body and into the app bar.
 *
 * The title used to be an `<h1>` rendered by `PageHeader` at the top of
 * `#app-page`, directly under a navbar whose entire left half was empty. That
 * cost every view ~44px of vertical space to say what the highlighted sidebar
 * item already said, and it hurt most on the app-like full-height views
 * (`/ask`), where the panel is sized off the remaining viewport.
 *
 * So the title moves into the app bar — the contextual top-app-bar pattern
 * (Material's top app bar; Linear, Notion, Vercel) — while the view keeps
 * owning the *value*. A module-level singleton (the `useMetadataEditMode`
 * pattern) rather than route `meta` because titles can be dynamic, and rather
 * than a `<Teleport>` because the target's existence would then be a mounting-
 * order dependency and per-breakpoint visibility classes on the `PageHeader`
 * root would no longer apply to the teleported node.
 *
 * Claims are **token-owned**: a claim records who made it, and a release is a
 * no-op unless the claimer still owns the title. Without that, a route change
 * that happened to mount the incoming view before unmounting the outgoing one
 * would leave the bar blank — a real ordering hazard that is invisible in
 * whichever order Vue happens to use today.
 *
 * Views without a `PageHeader` (document detail, note detail) deliberately
 * claim nothing: they carry their own hero title and a back link, so the bar
 * stays empty rather than repeating a section name that isn't where you are.
 */
const title = ref<string | null>(null)
const titleId = ref<string | undefined>(undefined)
let owner: symbol | null = null

export interface PageTitle {
  /** The app bar's current title, or null when no view has claimed one. */
  pageTitle: DeepReadonly<Ref<string | null>>
  /** Optional DOM id to put on the bar's `<h1>`, for views that expose it. */
  pageTitleId: DeepReadonly<Ref<string | undefined>>
  claimPageTitle: (token: symbol, value: string, id?: string) => void
  releasePageTitle: (token: symbol) => void
}

export function usePageTitle(): PageTitle {
  return {
    pageTitle: readonly(title),
    pageTitleId: readonly(titleId),
    claimPageTitle(token: symbol, value: string, id?: string): void {
      owner = token
      title.value = value
      titleId.value = id
    },
    releasePageTitle(token: symbol): void {
      if (owner !== token) return
      owner = null
      title.value = null
      titleId.value = undefined
    },
  }
}

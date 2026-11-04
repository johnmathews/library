<script setup lang="ts">
/**
 * The OAuth consent page (`/oauth/consent?request=<id>`).
 *
 * An MCP client (claude.ai, ChatGPT, Claude Code) sends the browser here via
 * `/mcp/authorize`. The page shows which app is asking, the host the answer
 * goes back to (the one fact a user can check against the app they started
 * from), who is signed in, and what the app will be able to do. Approve and
 * Deny each return a redirect URL; the browser goes there with
 * `window.location.assign`, handing the code (or `access_denied`) to the app.
 *
 * Not a public route: the auth guard sends a signed-in-less visitor to
 * `/login?redirect=…` and back with the query intact. Renders without the app
 * shell (like `/login`) because it is a stop on the way back to another app.
 * The API contract is docs/api.md §1.9.4.
 */
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { AppBanner, AppButton, AppErrorSummary } from '@/components/app'
import { ApiError } from '@/api/client'
import {
  approveAuthorizationRequest,
  denyAuthorizationRequest,
  getAuthorizationRequest,
  type OAuthRedirect,
  type OAuthRequest,
} from '@/api/oauth'
import { useAuthStore } from '@/stores/auth'

type State = 'loading' | 'ready' | 'expired' | 'not-found' | 'load-error'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()

const requestId = computed(() =>
  typeof route.query.request === 'string' ? route.query.request.trim() : '',
)

const state = ref<State>('loading')
const request = ref<OAuthRequest | null>(null)
/** The API's explanation for a 404, shown on the start-again state. */
const notFoundDetail = ref('')
const submitting = ref(false)
const submitError = ref<string | null>(null)

const START_AGAIN = 'Start connecting again from the app that sent you here.'

/** The app's registered name, or the host it redirects to when it gave none. */
const appName = computed(() => request.value?.client_name || request.value?.redirect_host || 'The app')

const userLabel = computed(() => {
  const user = auth.user
  if (!user) return ''
  return user.display_name && user.display_name !== user.username
    ? `${user.display_name} (${user.username})`
    : user.username
})
const userName = computed(() => auth.user?.display_name || auth.user?.username || 'you')

/** "unknown request; start again" → "Unknown request; start again." */
function asSentence(text: string): string {
  const trimmed = text.trim()
  if (!trimmed) return trimmed
  const capitalised = trimmed.charAt(0).toUpperCase() + trimmed.slice(1)
  return /[.!?]$/.test(capitalised) ? capitalised : `${capitalised}.`
}

const notFoundText = computed(() => asSentence(notFoundDetail.value))
// The API's 404 text already says to start again; don't say it twice.
const showStartAgain = computed(() => !/start connecting again/i.test(notFoundDetail.value))

function showNotFound(detail: string): void {
  notFoundDetail.value = detail
  state.value = 'not-found'
}

async function load(): Promise<void> {
  if (!requestId.value) {
    showNotFound('This link does not name a connection request')
    return
  }
  state.value = 'loading'
  try {
    request.value = await getAuthorizationRequest(requestId.value)
    state.value = request.value.expired ? 'expired' : 'ready'
  } catch (error: unknown) {
    if (error instanceof ApiError && error.status === 404) showNotFound(error.detail)
    else state.value = 'load-error'
  }
}

/**
 * Send the answer and follow the redirect. Buttons stay disabled after a
 * success: the browser is leaving, and the request is already consumed.
 */
async function answer(send: (id: string) => Promise<OAuthRedirect>): Promise<void> {
  if (submitting.value) return
  submitting.value = true
  submitError.value = null
  try {
    const { redirect_to } = await send(requestId.value)
    window.location.assign(redirect_to)
  } catch (error: unknown) {
    submitting.value = false
    if (error instanceof ApiError && error.status === 410) state.value = 'expired'
    else if (error instanceof ApiError && error.status === 404) showNotFound(error.detail)
    else submitError.value = 'Sorry, there is a problem with the service. Try again.'
  }
}

const approve = (): Promise<void> => answer(approveAuthorizationRequest)
const deny = (): Promise<void> => answer(denyAuthorizationRequest)

/** Sign out and come back here as someone else; the request stays pending. */
async function switchAccount(): Promise<void> {
  if (submitting.value) return
  submitting.value = true
  try {
    await auth.logout()
    await router.push({ name: 'login', query: { redirect: route.fullPath } })
  } catch {
    submitError.value = 'Sorry, signing out did not work. Try again.'
  } finally {
    submitting.value = false
  }
}

onMounted(load)

const labelClass =
  'block text-xs font-semibold uppercase tracking-wide text-gray-500 dark:text-gray-400'
const bodyClass = 'text-sm text-gray-600 dark:text-gray-300'
const headingClass = 'text-2xl font-bold text-gray-800 dark:text-gray-100'
</script>

<template>
  <div
    id="oauth-consent-page"
    class="min-h-[100dvh] flex items-start sm:items-center justify-center bg-gray-100 dark:bg-gray-900 px-4 py-8 sm:py-12"
  >
    <main
      id="oauth-consent-card"
      class="w-full max-w-lg bg-white dark:bg-gray-800 shadow-xs rounded-xl border border-gray-200 dark:border-gray-700/60 p-6 sm:p-8"
      aria-labelledby="oauth-consent-title"
    >
      <p class="text-sm font-semibold text-violet-500 mb-1">Library</p>

      <div v-if="state === 'loading'" id="oauth-consent-loading" data-testid="consent-loading" role="status">
        <h1 id="oauth-consent-title" :class="headingClass">Connect an app</h1>
        <p :class="bodyClass" class="mt-2">Loading the connection request…</p>
      </div>

      <div v-else-if="state === 'not-found'" id="oauth-consent-not-found" data-testid="consent-not-found">
        <h1 id="oauth-consent-title" :class="headingClass">This link can’t be used</h1>
        <p :class="bodyClass" class="mt-3" data-testid="consent-not-found-detail">{{ notFoundText }}</p>
        <p v-if="showStartAgain" :class="bodyClass" class="mt-2">{{ START_AGAIN }}</p>
        <div class="mt-6">
          <AppButton variant="secondary" :to="{ name: 'documents' }" data-testid="consent-home">
            Go to the Library
          </AppButton>
        </div>
      </div>

      <div v-else-if="state === 'expired'" id="oauth-consent-expired" data-testid="consent-expired">
        <h1 id="oauth-consent-title" :class="headingClass">This request has expired</h1>
        <p :class="bodyClass" class="mt-3">
          A request to connect is only valid for a few minutes. Return to {{ appName }} and start
          connecting again.
        </p>
        <p :class="bodyClass" class="mt-2">Returning tells {{ appName }} that access was not approved.</p>
        <AppErrorSummary
          v-if="submitError"
          class="mt-6"
          :errors="[{ text: submitError }]"
          data-testid="consent-submit-error"
        />
        <div class="mt-6">
          <AppButton
            type="button"
            variant="secondary"
            class="w-full sm:w-auto"
            :disabled="submitting"
            data-testid="consent-expired-return"
            @click="deny"
          >
            Return to {{ appName }}
          </AppButton>
        </div>
      </div>

      <div v-else-if="state === 'load-error'" id="oauth-consent-load-error" data-testid="consent-load-error">
        <h1 id="oauth-consent-title" :class="headingClass">Connect an app</h1>
        <div class="mt-4">
          <AppBanner variant="error" title="The request could not be loaded">
            <p :class="bodyClass">Sorry, there is a problem with the service. Try again.</p>
          </AppBanner>
        </div>
        <div class="mt-6">
          <AppButton type="button" variant="secondary" data-testid="consent-retry" @click="load">
            Try again
          </AppButton>
        </div>
      </div>

      <div v-else-if="request" id="oauth-consent-request" data-testid="consent-request">
        <h1 id="oauth-consent-title" :class="headingClass" class="break-words">
          Connect {{ appName }} to the Library?
        </h1>
        <p :class="bodyClass" class="mt-2">Approve only if you started connecting it just now.</p>

        <dl class="mt-6 space-y-5">
          <div id="oauth-consent-redirect" class="rounded-lg border border-violet-200 bg-violet-50 px-4 py-3 dark:border-violet-500/30 dark:bg-violet-500/10">
            <dt :class="labelClass">Sends you back to</dt>
            <dd class="mt-1">
              <span
                class="block break-all font-mono text-lg font-semibold text-gray-800 dark:text-gray-100"
                data-testid="consent-redirect-host"
              >{{ request.redirect_host }}</span>
              <span :class="bodyClass" class="mt-1 block">Check this is the app you started from.</span>
            </dd>
          </div>

          <div id="oauth-consent-account">
            <dt :class="labelClass">Connect as</dt>
            <dd class="mt-1">
              <span class="block text-base font-medium text-gray-800 dark:text-gray-100" data-testid="consent-account">
                {{ userLabel }}
              </span>
              <button
                type="button"
                class="mt-1 text-sm font-medium text-violet-600 hover:text-violet-700 dark:text-violet-400 dark:hover:text-violet-300 cursor-pointer"
                :disabled="submitting"
                data-testid="consent-switch-account"
                @click="switchAccount"
              >
                Not you? Sign in as someone else
              </button>
            </dd>
          </div>

          <div id="oauth-consent-access">
            <dt :class="labelClass">{{ appName }} will be able to</dt>
            <dd class="mt-2" data-testid="consent-access">
              <ul class="list-disc space-y-1 pl-5 text-sm text-gray-800 dark:text-gray-100">
                <li>Search and read every document in the Library, including ones others added</li>
                <li>Download document files</li>
                <li>Add new documents</li>
                <li>Change document details and add comments</li>
              </ul>
              <p :class="bodyClass" class="mt-3">
                Everything it adds or changes is recorded as {{ userName }}. Disconnect it at any
                time in Settings, under Connected apps.
              </p>
            </dd>
          </div>
        </dl>

        <AppErrorSummary
          v-if="submitError"
          class="mt-6"
          :errors="[{ text: submitError }]"
          data-testid="consent-submit-error"
        />

        <div id="oauth-consent-actions" class="mt-8 grid grid-cols-2 gap-3">
          <AppButton
            type="button"
            variant="secondary"
            :disabled="submitting"
            data-testid="consent-deny"
            @click="deny"
          >
            Deny
          </AppButton>
          <AppButton
            type="button"
            :disabled="submitting"
            data-testid="consent-approve"
            @click="approve"
          >
            Approve
          </AppButton>
        </div>
      </div>
    </main>
  </div>
</template>

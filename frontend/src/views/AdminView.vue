<script setup lang="ts">
/**
 * Admin console (route `/admin`, admin-only via the router guard).
 *
 * Four tabs, each backed by an /api/admin/* endpoint:
 *   - System: version/build, deployment topology, runtime config, DB stats.
 *   - Architecture: the project's architecture docs, markdown → sanitised HTML
 *     (same marked + DOMPurify pipeline as the note authoring/reader views).
 *   - Coverage: the latest CI-generated backend/frontend coverage figures.
 *   - Users: list every user, toggle admin/active, and create new users.
 * Tab selection is local state (no sub-routes).
 */
import { computed, onMounted, ref } from 'vue'
import { marked } from 'marked'
import DOMPurify from 'dompurify'
import { AppBadge, AppButton, AppInput } from '@/components/app'
import { ApiError } from '@/api/client'
import {
  createUser,
  getArchitecture,
  getCoverage,
  getSystemInfo,
  listUsers,
  updateUser,
  type AdminUser,
  type ArchitectureDoc,
  type CoverageInfo,
  type CoverageSide,
  type SystemInfo,
} from '@/api/admin'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()

type Tab = 'system' | 'architecture' | 'coverage' | 'users'
const tab = ref<Tab>('system')

const TABS: { id: Tab; label: string }[] = [
  { id: 'system', label: 'System' },
  { id: 'architecture', label: 'Architecture' },
  { id: 'coverage', label: 'Coverage' },
  { id: 'users', label: 'Users' },
]

// --- System -----------------------------------------------------------------

const system = ref<SystemInfo | null>(null)
const systemLoading = ref(true)
const systemError = ref<string | null>(null)

/** Config values can be any JSON; render objects as compact JSON, rest as text. */
function formatConfigValue(value: unknown): string {
  if (value === null) return 'null'
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}

const usdFormat = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' })
function formatUsd(amount: number): string {
  return usdFormat.format(amount)
}

const configEntries = computed(() => Object.entries(system.value?.config ?? {}))
const statusEntries = computed(() =>
  Object.entries(system.value?.stats.documents_by_status ?? {}),
)

async function loadSystem(): Promise<void> {
  systemLoading.value = true
  systemError.value = null
  try {
    system.value = await getSystemInfo()
  } catch {
    systemError.value = 'Could not load system information. Try refreshing the page.'
  } finally {
    systemLoading.value = false
  }
}

// --- Architecture -----------------------------------------------------------

const archDocs = ref<ArchitectureDoc[]>([])
const archLoading = ref(true)
const archError = ref<string | null>(null)
const selectedDocName = ref<string | null>(null)

const selectedDoc = computed<ArchitectureDoc | null>(
  () => archDocs.value.find((doc) => doc.name === selectedDocName.value) ?? null,
)

/** Sanitised HTML for the selected doc — identical pipeline to the note reader. */
const archHtml = computed(() =>
  selectedDoc.value
    ? DOMPurify.sanitize(marked.parse(selectedDoc.value.markdown, { async: false }) as string)
    : '',
)

async function loadArchitecture(): Promise<void> {
  archLoading.value = true
  archError.value = null
  try {
    const result = await getArchitecture()
    archDocs.value = result.docs
    selectedDocName.value = result.docs[0]?.name ?? null
  } catch {
    archError.value = 'Could not load architecture docs. Try refreshing the page.'
  } finally {
    archLoading.value = false
  }
}

// --- Coverage ---------------------------------------------------------------

const coverage = ref<CoverageInfo | null>(null)
const coverageLoading = ref(true)
const coverageError = ref<string | null>(null)

/** Render a percentage as "95.2%" or a dash when unmeasured. */
function formatPct(pct: number | null): string {
  return pct === null ? '—' : `${pct}%`
}

interface CoverageSideView {
  label: string
  side: CoverageSide | null
  /** Whether the side meets its gate; null when there is nothing to compare. */
  passes: boolean | null
}

/** Backend + frontend as a uniform list, each with its gate pass/fail verdict. */
const coverageSides = computed<CoverageSideView[]>(() => {
  const c = coverage.value
  if (!c) return []
  const toView = (label: string, side: CoverageSide | null): CoverageSideView => ({
    label,
    side,
    passes:
      side && side.pct !== null && side.threshold !== null ? side.pct >= side.threshold : null,
  })
  return [toView('Backend', c.backend), toView('Frontend', c.frontend)]
})

async function loadCoverage(): Promise<void> {
  coverageLoading.value = true
  coverageError.value = null
  try {
    coverage.value = await getCoverage()
  } catch {
    coverageError.value = 'Could not load coverage data. Try refreshing the page.'
  } finally {
    coverageLoading.value = false
  }
}

// --- Users ------------------------------------------------------------------

const users = ref<AdminUser[]>([])
const usersLoading = ref(true)
const usersError = ref<string | null>(null)
// Per-row action error (e.g. the last-admin 409 guard), keyed by user id.
const rowError = ref<Record<number, string>>({})
// Ids with an action in flight, to disable their buttons.
const pendingIds = ref<Set<number>>(new Set())

const dateFormat = new Intl.DateTimeFormat('en-GB', { dateStyle: 'medium' })
function formatDate(iso: string): string {
  const parsed = new Date(iso)
  return Number.isNaN(parsed.getTime()) ? iso : dateFormat.format(parsed)
}

function isCurrentUser(user: AdminUser): boolean {
  return auth.user?.id === user.id
}

async function loadUsers(): Promise<void> {
  usersLoading.value = true
  usersError.value = null
  try {
    users.value = await listUsers()
  } catch {
    usersError.value = 'Could not load users. Try refreshing the page.'
  } finally {
    usersLoading.value = false
  }
}

/** Apply a flag change to one user, surfacing a 409 (or other) error in-row. */
async function patchUser(
  user: AdminUser,
  body: { is_admin?: boolean; is_active?: boolean },
): Promise<void> {
  const next = new Set(pendingIds.value)
  next.add(user.id)
  pendingIds.value = next
  const nextErrors = { ...rowError.value }
  delete nextErrors[user.id]
  rowError.value = nextErrors
  try {
    const updated = await updateUser(user.id, body)
    users.value = users.value.map((u) => (u.id === updated.id ? updated : u))
  } catch (error) {
    rowError.value = {
      ...rowError.value,
      [user.id]:
        error instanceof ApiError ? error.detail : 'Could not update the user. Try again.',
    }
  } finally {
    const after = new Set(pendingIds.value)
    after.delete(user.id)
    pendingIds.value = after
  }
}

function toggleAdmin(user: AdminUser): void {
  void patchUser(user, { is_admin: !user.is_admin })
}

function toggleActive(user: AdminUser): void {
  void patchUser(user, { is_active: !user.is_active })
}

// --- Create-user form -------------------------------------------------------

const newUsername = ref('')
const newPassword = ref('')
const newDisplayName = ref('')
const newIsAdmin = ref(false)
const creating = ref(false)
const createError = ref<string | null>(null)

const canCreate = computed(
  () => newUsername.value.trim() !== '' && newPassword.value !== '' && !creating.value,
)

async function onCreateUser(): Promise<void> {
  if (!canCreate.value) return
  creating.value = true
  createError.value = null
  try {
    await createUser({
      username: newUsername.value.trim(),
      password: newPassword.value,
      display_name: newDisplayName.value.trim() || undefined,
      is_admin: newIsAdmin.value,
    })
    newUsername.value = ''
    newPassword.value = ''
    newDisplayName.value = ''
    newIsAdmin.value = false
    await loadUsers()
  } catch (error) {
    createError.value =
      error instanceof ApiError ? error.detail : 'Could not create the user. Try again.'
  } finally {
    creating.value = false
  }
}

onMounted(() => {
  void loadSystem()
  void loadArchitecture()
  void loadCoverage()
  void loadUsers()
})

const cardClass =
  'bg-white dark:bg-gray-800 shadow-xs rounded-xl border border-gray-200 dark:border-gray-700/60 p-6'
const tabClass = (active: boolean): string =>
  [
    'px-4 py-2 -mb-px text-sm font-medium border-b-2 transition cursor-pointer',
    active
      ? 'border-violet-500 text-violet-600'
      : 'border-transparent text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200',
  ].join(' ')
</script>

<template>
  <div id="admin-page" class="max-w-4xl">
    <h1 class="text-2xl md:text-3xl text-gray-800 dark:text-gray-100 font-bold mb-4">Admin</h1>

    <div
      role="tablist"
      class="flex gap-1 border-b border-gray-200 dark:border-gray-700/60 mb-6"
    >
      <button
        v-for="t in TABS"
        :key="t.id"
        role="tab"
        type="button"
        :aria-selected="tab === t.id"
        :tabindex="tab === t.id ? 0 : -1"
        :class="tabClass(tab === t.id)"
        :data-testid="`admin-tab-${t.id}-btn`"
        @click="tab = t.id"
      >
        {{ t.label }}
      </button>
    </div>

    <!-- System tab -->
    <section v-show="tab === 'system'" role="tabpanel" data-testid="admin-tab-system">
      <p v-if="systemLoading" data-testid="system-loading" class="text-gray-600 dark:text-gray-300">
        Loading system information…
      </p>
      <div
        v-else-if="systemError"
        data-testid="system-error"
        role="alert"
        class="bg-white dark:bg-gray-800 border-l-4 border-red-500 rounded-lg px-4 py-3 shadow-xs text-gray-700 dark:text-gray-200"
      >
        {{ systemError }}
      </div>
      <div v-else-if="system" class="space-y-6">
        <!-- Build + deployment -->
        <div :class="cardClass">
          <h2 class="text-lg font-semibold text-gray-800 dark:text-gray-100 mb-4">Build</h2>
          <dl class="grid grid-cols-1 sm:grid-cols-2 gap-3 text-sm">
            <div>
              <dt class="text-gray-500 dark:text-gray-400">Version</dt>
              <dd class="text-gray-800 dark:text-gray-100 font-medium" data-testid="system-version">
                {{ system.version }}
              </dd>
            </div>
            <div>
              <dt class="text-gray-500 dark:text-gray-400">Git SHA</dt>
              <dd
                class="text-gray-800 dark:text-gray-100 font-mono break-all"
                data-testid="system-git-sha"
              >
                {{ system.git_sha ?? '—' }}
              </dd>
            </div>
          </dl>

          <h3 class="text-sm font-semibold text-gray-700 dark:text-gray-200 mt-5 mb-2">
            Deployment
          </h3>
          <ul class="divide-y divide-gray-100 dark:divide-gray-700/60" data-testid="system-deployment">
            <li
              v-for="svc in system.deployment"
              :key="svc.name"
              class="flex items-center justify-between py-2 text-sm"
              data-testid="system-deployment-row"
            >
              <span class="text-gray-800 dark:text-gray-100 font-medium">{{ svc.name }}</span>
              <AppBadge colour="blue">{{ svc.role }}</AppBadge>
            </li>
          </ul>
        </div>

        <!-- Stats -->
        <div :class="cardClass">
          <h2 class="text-lg font-semibold text-gray-800 dark:text-gray-100 mb-4">Statistics</h2>
          <dl class="grid grid-cols-2 sm:grid-cols-3 gap-4 text-sm">
            <div>
              <dt class="text-gray-500 dark:text-gray-400">Documents</dt>
              <dd class="text-2xl font-bold text-gray-800 dark:text-gray-100" data-testid="stat-documents-total">
                {{ system.stats.documents_total }}
              </dd>
            </div>
            <div>
              <dt class="text-gray-500 dark:text-gray-400">Deleted</dt>
              <dd class="text-2xl font-bold text-gray-800 dark:text-gray-100" data-testid="stat-documents-deleted">
                {{ system.stats.documents_deleted }}
              </dd>
            </div>
            <div>
              <dt class="text-gray-500 dark:text-gray-400">Users (active)</dt>
              <dd class="text-2xl font-bold text-gray-800 dark:text-gray-100" data-testid="stat-users">
                {{ system.stats.users_active }} / {{ system.stats.users_total }}
              </dd>
            </div>
            <div>
              <dt class="text-gray-500 dark:text-gray-400">Jobs (active)</dt>
              <dd class="text-2xl font-bold text-gray-800 dark:text-gray-100" data-testid="stat-jobs">
                {{ system.stats.jobs_active }} / {{ system.stats.jobs_total }}
              </dd>
            </div>
            <div>
              <dt class="text-gray-500 dark:text-gray-400">Extraction cost</dt>
              <dd class="text-2xl font-bold text-gray-800 dark:text-gray-100" data-testid="stat-cost">
                {{ formatUsd(system.stats.extraction_cost_usd_total) }}
              </dd>
            </div>
          </dl>

          <h3 class="text-sm font-semibold text-gray-700 dark:text-gray-200 mt-5 mb-2">
            Documents by status
          </h3>
          <ul class="flex flex-wrap gap-2" data-testid="stat-by-status">
            <li
              v-for="[status, count] in statusEntries"
              :key="status"
              class="inline-flex items-center gap-1.5 rounded-full bg-gray-100 dark:bg-gray-700/40 px-3 py-1 text-sm"
            >
              <span class="text-gray-600 dark:text-gray-300">{{ status }}</span>
              <span class="font-semibold text-gray-800 dark:text-gray-100">{{ count }}</span>
            </li>
          </ul>
        </div>

        <!-- Config -->
        <div :class="cardClass">
          <h2 class="text-lg font-semibold text-gray-800 dark:text-gray-100 mb-4">Configuration</h2>
          <!-- Key above value (not a 2-col table): long keys and values both get
               the full width and wrap cleanly instead of cramping on a phone. -->
          <dl class="divide-y divide-gray-100 dark:divide-gray-700/60" data-testid="system-config">
            <div v-for="[key, value] in configEntries" :key="key" data-testid="system-config-row" class="py-2.5">
              <dt class="text-xs font-medium text-gray-500 dark:text-gray-400">{{ key }}</dt>
              <dd class="mt-0.5 text-sm text-gray-800 dark:text-gray-100 font-mono break-words">
                {{ formatConfigValue(value) }}
              </dd>
            </div>
          </dl>
        </div>
      </div>
    </section>

    <!-- Architecture tab -->
    <section v-show="tab === 'architecture'" role="tabpanel" data-testid="admin-tab-architecture">
      <p v-if="archLoading" data-testid="arch-loading" class="text-gray-600 dark:text-gray-300">
        Loading architecture docs…
      </p>
      <div
        v-else-if="archError"
        data-testid="arch-error"
        role="alert"
        class="bg-white dark:bg-gray-800 border-l-4 border-red-500 rounded-lg px-4 py-3 shadow-xs text-gray-700 dark:text-gray-200"
      >
        {{ archError }}
      </div>
      <p
        v-else-if="archDocs.length === 0"
        data-testid="arch-empty"
        class="text-sm text-gray-500 dark:text-gray-400"
      >
        No architecture documents are available.
      </p>
      <div v-else class="space-y-4">
        <div class="flex flex-wrap gap-2" data-testid="arch-doc-list">
          <button
            v-for="doc in archDocs"
            :key="doc.name"
            type="button"
            :data-testid="`arch-doc-${doc.name}`"
            :class="[
              'rounded-lg border px-3 py-1.5 text-sm font-medium transition cursor-pointer',
              selectedDocName === doc.name
                ? 'border-violet-500 ring-2 ring-violet-500/30 text-violet-600 dark:text-violet-300'
                : 'border-gray-200 dark:border-gray-700/60 text-gray-700 dark:text-gray-200 hover:border-gray-300 dark:hover:border-gray-600',
            ]"
            @click="selectedDocName = doc.name"
          >
            {{ doc.title }}
          </button>
        </div>

        <div v-if="selectedDoc" :class="cardClass">
          <h2 class="text-lg font-semibold text-gray-800 dark:text-gray-100 mb-4">
            {{ selectedDoc.title }}
          </h2>
          <!-- eslint-disable-next-line vue/no-v-html -- sanitized via DOMPurify in archHtml -->
          <div
            class="doc-markdown text-gray-800 dark:text-gray-100"
            data-testid="arch-content"
            v-html="archHtml"
          />
        </div>
      </div>
    </section>

    <!-- Coverage tab -->
    <section v-show="tab === 'coverage'" role="tabpanel" data-testid="admin-tab-coverage">
      <p v-if="coverageLoading" data-testid="coverage-loading" class="text-gray-600 dark:text-gray-300">
        Loading coverage data…
      </p>
      <div
        v-else-if="coverageError"
        data-testid="coverage-error"
        role="alert"
        class="bg-white dark:bg-gray-800 border-l-4 border-red-500 rounded-lg px-4 py-3 shadow-xs text-gray-700 dark:text-gray-200"
      >
        {{ coverageError }}
      </div>
      <div
        v-else-if="coverage && !coverage.available"
        data-testid="coverage-unavailable"
        class="bg-white dark:bg-gray-800 border-l-4 border-yellow-500 rounded-lg px-4 py-3 shadow-xs text-gray-700 dark:text-gray-200"
      >
        Coverage data unavailable (generated by CI).
      </div>
      <div v-else-if="coverage" class="space-y-6">
        <!-- One card per side: headline %, gate pass/fail, file counts, and the
             lowest-covered files so the gate figure is actionable. -->
        <div class="grid grid-cols-1 sm:grid-cols-2 gap-6">
          <div
            v-for="s in coverageSides"
            :key="s.label"
            :class="cardClass"
            :data-testid="`coverage-card-${s.label.toLowerCase()}`"
          >
            <div class="flex items-center justify-between gap-3 mb-3">
              <h2 class="text-lg font-semibold text-gray-800 dark:text-gray-100">{{ s.label }}</h2>
              <AppBadge v-if="s.passes !== null" :colour="s.passes ? 'green' : 'red'">
                {{ s.passes ? 'Pass' : 'Below gate' }}
              </AppBadge>
            </div>
            <p
              class="text-3xl font-bold text-gray-800 dark:text-gray-100"
              :data-testid="`coverage-${s.label.toLowerCase()}`"
            >
              {{ formatPct(s.side?.pct ?? null) }}
            </p>
            <p class="mt-1 text-sm text-gray-500 dark:text-gray-400">
              <span v-if="s.side?.threshold != null">Gate {{ s.side.threshold }}%</span>
              <span v-if="s.side?.files_total != null"> · {{ s.side.files_total }} files</span>
              <span v-if="s.side?.files_below_gate != null">
                · {{ s.side.files_below_gate }} below gate
              </span>
            </p>

            <div v-if="s.side && s.side.worst_files.length" class="mt-4">
              <h3 class="mb-2 text-sm font-semibold text-gray-700 dark:text-gray-200">
                Lowest-covered files
              </h3>
              <ul class="divide-y divide-gray-100 dark:divide-gray-700/60">
                <li
                  v-for="f in s.side.worst_files"
                  :key="f.path"
                  class="flex items-center justify-between gap-3 py-1.5 text-sm"
                >
                  <span class="min-w-0 truncate font-mono text-xs text-gray-600 dark:text-gray-300">
                    {{ f.path }}
                  </span>
                  <span
                    class="shrink-0 font-semibold"
                    :class="
                      s.side.threshold != null && f.pct < s.side.threshold
                        ? 'text-red-600 dark:text-red-400'
                        : 'text-gray-800 dark:text-gray-100'
                    "
                  >
                    {{ formatPct(f.pct) }}
                  </span>
                </li>
              </ul>
            </div>
          </div>
        </div>

        <!-- When + which build produced these numbers. -->
        <div :class="cardClass">
          <dl class="grid grid-cols-1 sm:grid-cols-2 gap-4 text-sm">
            <div>
              <dt class="text-gray-500 dark:text-gray-400">Generated</dt>
              <dd class="text-gray-800 dark:text-gray-100" data-testid="coverage-generated">
                {{ coverage.generated_at ?? '—' }}
              </dd>
            </div>
            <div>
              <dt class="text-gray-500 dark:text-gray-400">Git SHA</dt>
              <dd
                class="text-gray-800 dark:text-gray-100 font-mono break-all"
                data-testid="coverage-git-sha"
              >
                {{ coverage.git_sha ?? '—' }}
              </dd>
            </div>
          </dl>
        </div>
      </div>
    </section>

    <!-- Users tab -->
    <section v-show="tab === 'users'" role="tabpanel" data-testid="admin-tab-users">
      <p v-if="usersLoading" data-testid="users-loading" class="text-gray-600 dark:text-gray-300">
        Loading users…
      </p>
      <div
        v-else-if="usersError"
        data-testid="users-error"
        role="alert"
        class="bg-white dark:bg-gray-800 border-l-4 border-red-500 rounded-lg px-4 py-3 shadow-xs text-gray-700 dark:text-gray-200"
      >
        {{ usersError }}
      </div>
      <div v-else class="space-y-6">
        <div class="overflow-x-auto bg-white dark:bg-gray-800 rounded-lg shadow-xs">
          <table class="w-full text-sm">
            <thead
              class="text-xs uppercase text-gray-400 dark:text-gray-500 border-b border-gray-100 dark:border-gray-700/60"
            >
              <tr>
                <th class="text-left font-semibold px-4 py-3">Username</th>
                <th class="text-left font-semibold px-4 py-3">Display name</th>
                <th class="text-left font-semibold px-4 py-3">Role</th>
                <th class="text-left font-semibold px-4 py-3">Status</th>
                <th class="text-left font-semibold px-4 py-3">Created</th>
                <th class="text-left font-semibold px-4 py-3">Actions</th>
              </tr>
            </thead>
            <tbody class="divide-y divide-gray-100 dark:divide-gray-700/60">
              <tr
                v-for="user in users"
                :key="user.id"
                :data-testid="`user-row-${user.id}`"
              >
                <td class="px-4 py-3 text-gray-800 dark:text-gray-100 font-medium">
                  {{ user.username }}
                </td>
                <td class="px-4 py-3 text-gray-600 dark:text-gray-300">{{ user.display_name }}</td>
                <td class="px-4 py-3">
                  <AppBadge :colour="user.is_admin ? 'purple' : 'grey'">
                    {{ user.is_admin ? 'Admin' : 'User' }}
                  </AppBadge>
                </td>
                <td class="px-4 py-3">
                  <AppBadge :colour="user.is_active ? 'green' : 'red'">
                    {{ user.is_active ? 'Active' : 'Inactive' }}
                  </AppBadge>
                </td>
                <td class="px-4 py-3 text-gray-600 dark:text-gray-300 whitespace-nowrap">
                  {{ formatDate(user.created_at) }}
                </td>
                <td class="px-4 py-3">
                  <div v-if="isCurrentUser(user)" class="text-xs text-gray-400 dark:text-gray-500">
                    You
                  </div>
                  <div v-else class="flex flex-wrap gap-2">
                    <button
                      type="button"
                      :data-testid="`user-toggle-admin-${user.id}`"
                      :disabled="pendingIds.has(user.id)"
                      class="rounded-md border border-gray-200 dark:border-gray-700/60 px-2.5 py-1 text-xs font-medium text-gray-700 dark:text-gray-200 hover:bg-gray-50 dark:hover:bg-gray-700 disabled:opacity-50 cursor-pointer"
                      @click="toggleAdmin(user)"
                    >
                      {{ user.is_admin ? 'Demote' : 'Promote' }}
                    </button>
                    <button
                      type="button"
                      :data-testid="`user-toggle-active-${user.id}`"
                      :disabled="pendingIds.has(user.id)"
                      class="rounded-md border border-gray-200 dark:border-gray-700/60 px-2.5 py-1 text-xs font-medium text-gray-700 dark:text-gray-200 hover:bg-gray-50 dark:hover:bg-gray-700 disabled:opacity-50 cursor-pointer"
                      @click="toggleActive(user)"
                    >
                      {{ user.is_active ? 'Deactivate' : 'Activate' }}
                    </button>
                  </div>
                  <p
                    v-if="rowError[user.id]"
                    :data-testid="`user-error-${user.id}`"
                    class="mt-1 text-xs text-red-600 dark:text-red-400"
                  >
                    {{ rowError[user.id] }}
                  </p>
                </td>
              </tr>
            </tbody>
          </table>
        </div>

        <!-- Create user -->
        <div :class="cardClass">
          <h2 class="text-lg font-semibold text-gray-800 dark:text-gray-100 mb-4">Create user</h2>
          <div
            v-if="createError"
            data-testid="create-user-error"
            role="alert"
            class="mb-4 border-l-4 border-red-500 bg-red-50 dark:bg-red-500/10 rounded-lg px-4 py-3 text-sm text-red-700 dark:text-red-300"
          >
            {{ createError }}
          </div>
          <form class="space-y-4" data-testid="create-user-form" @submit.prevent="onCreateUser">
            <AppInput id="new-username" v-model="newUsername" label="Username" autocomplete="off" />
            <AppInput
              id="new-password"
              v-model="newPassword"
              type="password"
              label="Password"
              autocomplete="new-password"
            />
            <AppInput
              id="new-display-name"
              v-model="newDisplayName"
              label="Display name (optional)"
              autocomplete="off"
            />
            <label class="flex items-center gap-2">
              <input
                v-model="newIsAdmin"
                type="checkbox"
                data-testid="new-is-admin"
                class="rounded border-gray-300 dark:border-gray-600 text-violet-500 focus:ring-violet-500"
              />
              <span class="text-sm font-medium text-gray-700 dark:text-gray-300">Administrator</span>
            </label>
            <AppButton type="submit" data-testid="create-user-submit" :disabled="!canCreate">
              {{ creating ? 'Creating…' : 'Create user' }}
            </AppButton>
          </form>
        </div>
      </div>
    </section>
  </div>
</template>

<style scoped>
/* Markdown rendered via v-html; restore prose spacing stripped by Tailwind
   preflight (mirrors .doc-markdown in NewNoteView.vue / DocumentDetailView). */
.doc-markdown :deep(p) {
  margin-bottom: 0.75rem;
}
.doc-markdown :deep(p:last-child) {
  margin-bottom: 0;
}
.doc-markdown :deep(strong) {
  font-weight: 600;
}
.doc-markdown :deep(em) {
  font-style: italic;
}
.doc-markdown :deep(ul),
.doc-markdown :deep(ol) {
  margin: 0.5rem 0 0.75rem;
  padding-left: 1.5rem;
}
.doc-markdown :deep(ul) {
  list-style: disc;
}
.doc-markdown :deep(ol) {
  list-style: decimal;
}
.doc-markdown :deep(li) {
  margin-bottom: 0.25rem;
}
.doc-markdown :deep(h1),
.doc-markdown :deep(h2),
.doc-markdown :deep(h3) {
  font-weight: 600;
  margin: 0.75rem 0 0.5rem;
}
.doc-markdown :deep(code) {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 0.875em;
  padding: 0.1em 0.3em;
  border-radius: 0.25rem;
  background: rgb(0 0 0 / 0.06);
}
.dark .doc-markdown :deep(code) {
  background: rgb(255 255 255 / 0.08);
}
/* Fenced code blocks (incl. wide ASCII diagrams) scroll horizontally inside the
   block instead of overflowing the card and the viewport on a phone. */
.doc-markdown :deep(pre) {
  margin: 0.75rem 0;
  padding: 0.75rem 1rem;
  border-radius: 0.5rem;
  background: rgb(0 0 0 / 0.06);
  overflow-x: auto;
}
.dark .doc-markdown :deep(pre) {
  background: rgb(255 255 255 / 0.08);
}
.doc-markdown :deep(pre code) {
  padding: 0;
  background: none;
  white-space: pre;
}
</style>

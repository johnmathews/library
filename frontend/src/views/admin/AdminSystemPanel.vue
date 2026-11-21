<script setup lang="ts">
/**
 * System tab: version/build, deployment topology, runtime config, DB stats.
 * Loads eagerly on mount (parent keeps every panel mounted via v-show).
 */
import { computed, onMounted, ref } from 'vue'
import { AppBadge, AppBanner } from '@/components/app'
import { getSystemInfo, type SystemInfo } from '@/api/admin'

const cardClass = 'card p-6'

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

onMounted(() => {
  void loadSystem()
})
</script>

<template>
  <p v-if="systemLoading" data-testid="system-loading" class="text-ink-secondary">
    Loading system information…
  </p>
  <AppBanner v-else-if="systemError" variant="error" data-testid="system-error">
    {{ systemError }}
  </AppBanner>
  <div v-else-if="system" class="space-y-6">
    <!-- Build + deployment -->
    <div :class="cardClass">
      <h2 class="text-base font-[650] text-ink mb-4">Build</h2>
      <dl class="grid grid-cols-1 sm:grid-cols-2 gap-3 text-sm">
        <div>
          <dt class="text-ink-secondary">Version</dt>
          <dd class="text-ink font-medium" data-testid="system-version">
            {{ system.version }}
          </dd>
        </div>
        <div>
          <dt class="text-ink-secondary">Git SHA</dt>
          <dd
            class="text-ink font-mono break-all"
            data-testid="system-git-sha"
          >
            {{ system.git_sha ?? '—' }}
          </dd>
        </div>
      </dl>

      <h3 class="text-sm font-semibold text-ink mt-5 mb-2">
        Deployment
      </h3>
      <ul class="divide-y divide-line" data-testid="system-deployment">
        <li
          v-for="svc in system.deployment"
          :key="svc.name"
          class="flex items-center justify-between py-2 text-sm"
          data-testid="system-deployment-row"
        >
          <span class="text-ink font-medium">{{ svc.name }}</span>
          <AppBadge>{{ svc.role }}</AppBadge>
        </li>
      </ul>
    </div>

    <!-- Stats -->
    <div :class="cardClass">
      <h2 class="text-base font-[650] text-ink mb-4">Statistics</h2>
      <dl class="grid grid-cols-2 sm:grid-cols-3 gap-4 text-sm">
        <div>
          <dt class="text-ink-secondary">Documents</dt>
          <dd class="text-2xl font-[650] text-ink tabular-nums" data-testid="stat-documents-total">
            {{ system.stats.documents_total }}
          </dd>
        </div>
        <div>
          <dt class="text-ink-secondary">Deleted</dt>
          <dd class="text-2xl font-[650] text-ink tabular-nums" data-testid="stat-documents-deleted">
            {{ system.stats.documents_deleted }}
          </dd>
        </div>
        <div>
          <dt class="text-ink-secondary">Users (active)</dt>
          <dd class="text-2xl font-[650] text-ink tabular-nums" data-testid="stat-users">
            {{ system.stats.users_active }} / {{ system.stats.users_total }}
          </dd>
        </div>
        <div>
          <dt class="text-ink-secondary">Jobs (active)</dt>
          <dd class="text-2xl font-[650] text-ink tabular-nums" data-testid="stat-jobs">
            {{ system.stats.jobs_active }} / {{ system.stats.jobs_total }}
          </dd>
        </div>
        <div>
          <dt class="text-ink-secondary">Extraction cost</dt>
          <dd class="text-2xl font-[650] text-ink tabular-nums" data-testid="stat-cost">
            {{ formatUsd(system.stats.extraction_cost_usd_total) }}
          </dd>
        </div>
        <div>
          <dt class="text-ink-secondary" title="Extraction/markdown last skipped by the daily LLM budget — run `library backfill` to fill them">
            Budget-skipped
          </dt>
          <dd
            class="text-2xl font-[650]"
            :class="system.stats.documents_budget_skipped > 0 ? 'text-attention' : 'text-ink'"
            data-testid="stat-budget-skipped"
          >
            {{ system.stats.documents_budget_skipped }}
          </dd>
        </div>
      </dl>

      <h3 class="text-sm font-semibold text-ink mt-5 mb-2">
        Documents by status
      </h3>
      <ul class="flex flex-wrap gap-2" data-testid="stat-by-status">
        <li
          v-for="[status, count] in statusEntries"
          :key="status"
          class="inline-flex items-center gap-1.5 rounded-full bg-tile px-3 py-1 text-sm"
        >
          <span class="text-ink-secondary">{{ status }}</span>
          <span class="font-semibold text-ink">{{ count }}</span>
        </li>
      </ul>
    </div>

    <!-- Config -->
    <div :class="cardClass">
      <h2 class="text-base font-[650] text-ink mb-4">Configuration</h2>
      <!-- Key above value (not a 2-col table): long keys and values both get
           the full width and wrap cleanly instead of cramping on a phone. -->
      <dl class="divide-y divide-line" data-testid="system-config">
        <div v-for="[key, value] in configEntries" :key="key" data-testid="system-config-row" class="py-2.5">
          <dt class="text-xs font-medium text-ink-secondary">{{ key }}</dt>
          <dd class="mt-0.5 text-sm text-ink font-mono break-words">
            {{ formatConfigValue(value) }}
          </dd>
        </div>
      </dl>
    </div>
  </div>
</template>

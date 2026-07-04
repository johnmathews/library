<script setup lang="ts">
/**
 * Admin console (route `/admin`, admin-only via the router guard).
 *
 * Tabs (in display order), each backed by an /api/admin/* endpoint:
 *   - Users: list every user, toggle admin/active, create and delete users.
 *   - Metadata: senders / recipients / kinds management.
 *   - Architecture: the project's architecture docs, markdown → sanitised HTML
 *     (same marked + DOMPurify pipeline as the note authoring/reader views).
 *   - Coverage: the latest CI-generated coverage figures per test type.
 *   - System: version/build, deployment topology, runtime config, DB stats.
 * Tab selection is local state (no sub-routes).
 */
import { ref } from 'vue'
import { PageHeader } from '@/components/app'
import AdminArchitecturePanel from './admin/AdminArchitecturePanel.vue'
import AdminCoveragePanel from './admin/AdminCoveragePanel.vue'
import AdminMetadataPanel from './admin/AdminMetadataPanel.vue'
import AdminSystemPanel from './admin/AdminSystemPanel.vue'
import AdminUsersPanel from './admin/AdminUsersPanel.vue'

type Tab = 'users' | 'metadata' | 'architecture' | 'coverage' | 'system'
const tab = ref<Tab>('users')

const TABS: { id: Tab; label: string }[] = [
  { id: 'users', label: 'Users' },
  { id: 'metadata', label: 'Metadata' },
  { id: 'architecture', label: 'Architecture' },
  { id: 'coverage', label: 'Coverage' },
  { id: 'system', label: 'System' },
]

const tabClass = (active: boolean): string =>
  [
    'px-4 py-2 -mb-px text-sm font-medium border-b-2 transition cursor-pointer',
    active
      ? 'border-violet-500 text-violet-600'
      : 'border-transparent text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200',
  ].join(' ')
</script>

<template>
  <div id="admin-page">
    <PageHeader title="Admin" />

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
      <AdminSystemPanel />
    </section>

    <!-- Architecture tab -->
    <section v-show="tab === 'architecture'" role="tabpanel" data-testid="admin-tab-architecture">
      <AdminArchitecturePanel />
    </section>

    <!-- Coverage tab -->
    <section v-show="tab === 'coverage'" role="tabpanel" data-testid="admin-tab-coverage">
      <AdminCoveragePanel />
    </section>

    <!-- Users tab -->
    <section v-show="tab === 'users'" role="tabpanel" data-testid="admin-tab-users">
      <AdminUsersPanel />
    </section>

    <!-- Metadata tab (senders / recipients / kinds management) -->
    <section v-show="tab === 'metadata'" role="tabpanel" data-testid="admin-tab-metadata">
      <AdminMetadataPanel :active="tab === 'metadata'" />
    </section>
  </div>
</template>

<script setup lang="ts">
/**
 * Users tab: list every user, toggle admin/active, create and delete users.
 * Loads eagerly on mount (parent keeps every panel mounted via v-show).
 */
import { computed, onMounted, ref } from 'vue'
import { AppBadge, AppBanner, AppButton, AppInput } from '@/components/app'
import { ApiError } from '@/api/client'
import { createUser, deleteUser, listUsers, updateUser, type AdminUser } from '@/api/admin'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()

const cardClass = 'card p-6'

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

// Inline two-step delete: the id of the user whose Delete button was armed (so
// a second click confirms), rather than a native blocking confirm() dialog.
const confirmingDeleteId = ref<number | null>(null)

function requestDeleteUser(user: AdminUser): void {
  confirmingDeleteId.value = user.id
}

function cancelDeleteUser(): void {
  confirmingDeleteId.value = null
}

/** Delete a user, surfacing a guard error (400 self / 409 last-admin) in-row. */
async function confirmDeleteUser(user: AdminUser): Promise<void> {
  const next = new Set(pendingIds.value)
  next.add(user.id)
  pendingIds.value = next
  const nextErrors = { ...rowError.value }
  delete nextErrors[user.id]
  rowError.value = nextErrors
  try {
    await deleteUser(user.id)
    users.value = users.value.filter((u) => u.id !== user.id)
    confirmingDeleteId.value = null
  } catch (error) {
    rowError.value = {
      ...rowError.value,
      [user.id]:
        error instanceof ApiError ? error.detail : 'Could not delete the user. Try again.',
    }
  } finally {
    const after = new Set(pendingIds.value)
    after.delete(user.id)
    pendingIds.value = after
  }
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
  void loadUsers()
})
</script>

<template>
  <p v-if="usersLoading" data-testid="users-loading" class="text-gray-600 dark:text-gray-300">
    Loading users…
  </p>
  <AppBanner v-else-if="usersError" variant="error" data-testid="users-error">
    {{ usersError }}
  </AppBanner>
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
                <template v-if="confirmingDeleteId === user.id">
                  <button
                    type="button"
                    :data-testid="`user-delete-confirm-${user.id}`"
                    :disabled="pendingIds.has(user.id)"
                    class="rounded-md border border-red-500 bg-red-500 px-2.5 py-1 text-xs font-medium text-white hover:bg-red-600 disabled:opacity-50 cursor-pointer"
                    @click="confirmDeleteUser(user)"
                  >
                    Confirm delete
                  </button>
                  <button
                    type="button"
                    :data-testid="`user-delete-cancel-${user.id}`"
                    :disabled="pendingIds.has(user.id)"
                    class="rounded-md border border-gray-200 dark:border-gray-700/60 px-2.5 py-1 text-xs font-medium text-gray-700 dark:text-gray-200 hover:bg-gray-50 dark:hover:bg-gray-700 disabled:opacity-50 cursor-pointer"
                    @click="cancelDeleteUser"
                  >
                    Cancel
                  </button>
                </template>
                <button
                  v-else
                  type="button"
                  :data-testid="`user-delete-${user.id}`"
                  :disabled="pendingIds.has(user.id)"
                  class="rounded-md border border-red-200 dark:border-red-500/40 px-2.5 py-1 text-xs font-medium text-red-600 dark:text-red-400 hover:bg-red-50 dark:hover:bg-red-500/10 disabled:opacity-50 cursor-pointer"
                  @click="requestDeleteUser(user)"
                >
                  Delete
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
</template>

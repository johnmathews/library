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
  <p v-if="usersLoading" data-testid="users-loading" class="text-ink-secondary">
    Loading users…
  </p>
  <AppBanner v-else-if="usersError" variant="error" data-testid="users-error">
    {{ usersError }}
  </AppBanner>
  <div v-else class="space-y-6">
    <div class="card overflow-x-auto">
      <table class="w-full text-sm">
        <thead class="text-[13px] text-ink-secondary border-b border-line">
          <tr>
            <th class="text-left font-semibold px-4 py-3">Username</th>
            <th class="text-left font-semibold px-4 py-3">Display name</th>
            <th class="text-left font-semibold px-4 py-3">Role</th>
            <th class="text-left font-semibold px-4 py-3">Status</th>
            <th class="text-left font-semibold px-4 py-3">Created</th>
            <th class="text-left font-semibold px-4 py-3">Actions</th>
          </tr>
        </thead>
        <tbody class="divide-y divide-line">
          <tr
            v-for="user in users"
            :key="user.id"
            :data-testid="`user-row-${user.id}`"
            class="hover:bg-tile"
          >
            <td class="px-4 py-3 text-ink font-medium">
              {{ user.username }}
            </td>
            <td class="px-4 py-3 text-ink-secondary">{{ user.display_name }}</td>
            <td class="px-4 py-3">
              <AppBadge>
                {{ user.is_admin ? 'Admin' : 'User' }}
              </AppBadge>
            </td>
            <td class="px-4 py-3">
              <AppBadge :colour="user.is_active ? 'green' : 'grey'">
                {{ user.is_active ? 'Active' : 'Inactive' }}
              </AppBadge>
            </td>
            <td class="px-4 py-3 text-ink-secondary whitespace-nowrap">
              {{ formatDate(user.created_at) }}
            </td>
            <td class="px-4 py-3">
              <div v-if="isCurrentUser(user)" class="text-xs text-ink-secondary">
                You
              </div>
              <div v-else class="flex flex-wrap gap-2">
                <button
                  type="button"
                  :data-testid="`user-toggle-admin-${user.id}`"
                  :disabled="pendingIds.has(user.id)"
                  class="rounded-sm border border-control-border bg-panel px-2.5 py-1 text-xs font-medium text-ink hover:bg-tile disabled:opacity-50 cursor-pointer"
                  @click="toggleAdmin(user)"
                >
                  {{ user.is_admin ? 'Demote' : 'Promote' }}
                </button>
                <button
                  type="button"
                  :data-testid="`user-toggle-active-${user.id}`"
                  :disabled="pendingIds.has(user.id)"
                  class="rounded-sm border border-control-border bg-panel px-2.5 py-1 text-xs font-medium text-ink hover:bg-tile disabled:opacity-50 cursor-pointer"
                  @click="toggleActive(user)"
                >
                  {{ user.is_active ? 'Deactivate' : 'Activate' }}
                </button>
                <template v-if="confirmingDeleteId === user.id">
                  <button
                    type="button"
                    :data-testid="`user-delete-confirm-${user.id}`"
                    :disabled="pendingIds.has(user.id)"
                    class="rounded-sm border border-control-border bg-panel px-2.5 py-1 text-xs font-medium text-ink hover:bg-tile disabled:opacity-50 cursor-pointer"
                    @click="confirmDeleteUser(user)"
                  >
                    Confirm delete
                  </button>
                  <button
                    type="button"
                    :data-testid="`user-delete-cancel-${user.id}`"
                    :disabled="pendingIds.has(user.id)"
                    class="rounded-sm border border-control-border bg-panel px-2.5 py-1 text-xs font-medium text-ink hover:bg-tile disabled:opacity-50 cursor-pointer"
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
                  class="rounded-sm border border-control-border bg-panel px-2.5 py-1 text-xs font-medium text-ink hover:bg-tile disabled:opacity-50 cursor-pointer"
                  @click="requestDeleteUser(user)"
                >
                  Delete
                </button>
              </div>
              <p
                v-if="rowError[user.id]"
                :data-testid="`user-error-${user.id}`"
                class="mt-1 text-xs text-error-ink"
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
      <h2 class="text-base font-[650] text-ink mb-4">Create user</h2>
      <div
        v-if="createError"
        data-testid="create-user-error"
        role="alert"
        class="mb-4 bg-deficit-tint rounded-md px-4 py-3 text-sm text-error-ink"
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
            class="form-checkbox text-brand-fill"
          />
          <span class="text-sm font-medium text-ink">Administrator</span>
        </label>
        <AppButton type="submit" data-testid="create-user-submit" :disabled="!canCreate">
          {{ creating ? 'Creating…' : 'Create user' }}
        </AppButton>
      </form>
    </div>
  </div>
</template>

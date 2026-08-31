import {
  createRouter,
  createWebHistory,
  type RouteLocationNormalized,
  type RouteRecordRaw,
} from 'vue-router'
import DocumentListView from '../views/DocumentListView.vue'
import { useAuthStore } from '@/stores/auth'

export const routes: RouteRecordRaw[] = [
  {
    path: '/',
    name: 'documents',
    component: DocumentListView,
  },
  {
    path: '/documents/:id',
    name: 'document-detail',
    component: () => import('../views/DocumentDetailView.vue'),
  },
  {
    // GOV.UK pattern: destructive actions get a confirmation PAGE with its
    // own URL (back-button friendly), never a JS-only modal.
    path: '/documents/:id/delete',
    name: 'document-delete',
    component: () => import('../views/DocumentDeleteView.vue'),
  },
  {
    path: '/deleted',
    name: 'documents-deleted',
    component: () => import('../views/RecentlyDeletedView.vue'),
  },
  {
    // Hold-for-review queue: emails the ingest pipeline held instead of filing.
    path: '/held-emails',
    name: 'held-emails',
    component: () => import('../views/HeldEmailsView.vue'),
  },
  {
    path: '/upload',
    name: 'upload',
    component: () => import('../views/UploadView.vue'),
  },
  {
    path: '/notes/new',
    name: 'note-new',
    component: () => import('../views/NewNoteView.vue'),
  },
  {
    path: '/ask',
    name: 'ask',
    component: () => import('../views/AskView.vue'),
  },
  {
    // A fresh conversation (mobile: the chat screen with the empty state). Must
    // be declared before the numeric `:threadId` route; the digit constraint on
    // that route means `new` can never be swallowed as a thread id.
    path: '/ask/new',
    name: 'ask-new',
    component: () => import('../views/AskView.vue'),
  },
  {
    path: '/ask/:threadId(\\d+)',
    name: 'ask-thread',
    component: () => import('../views/AskView.vue'),
  },
  {
    path: '/settings',
    name: 'settings',
    component: () => import('../views/SettingsView.vue'),
  },
  {
    // The facet vocabulary: the closed set every chart splits by, plus the
    // split colours. Its CRUD routes are authenticated but not admin-gated, so
    // neither is this (docs/facets.md).
    path: '/vocabulary',
    name: 'vocabulary',
    component: () => import('../views/VocabularyView.vue'),
  },
  {
    // GOV.UK pattern, as on document-delete: a destructive, irreversible action
    // gets a confirmation PAGE with its own URL, never a modal.
    path: '/vocabulary/:facetKey/:valueKey/merge',
    name: 'vocabulary-merge',
    component: () => import('../views/vocabulary/ValueMergeView.vue'),
  },
  {
    path: '/jobs',
    name: 'jobs',
    component: () => import('../views/JobsView.vue'),
  },
  {
    path: '/charts',
    name: 'charts',
    component: () => import('../views/SpendingBoardView.vue'),
  },
  {
    // Digit-constrained as convention, matching `/ask/:threadId(\d+)` above;
    // no other `/charts/*` route remains to collide with.
    // router/__tests__/spending-routes.spec.ts pins the resolution.
    path: '/charts/:chartId(\\d+)',
    name: 'spending-workspace',
    component: () => import('../views/SpendingWorkspaceView.vue'),
  },
  {
    path: '/projects',
    name: 'projects',
    component: () => import('../views/ProjectsListView.vue'),
  },
  {
    path: '/matters',
    name: 'matters',
    component: () => import('../views/MattersListView.vue'),
  },
  {
    path: '/saved-views',
    name: 'saved-views',
    component: () => import('../views/SavedViewsView.vue'),
  },
  {
    path: '/admin',
    name: 'admin',
    component: () => import('../views/AdminView.vue'),
    meta: { adminOnly: true },
  },
  {
    path: '/login',
    name: 'login',
    component: () => import('../views/LoginView.vue'),
    meta: { public: true },
  },
]

/**
 * Authentication guard: every non-public route requires a session. The
 * auth store caches GET /api/auth/me, so this costs one request per page
 * load. Unauthenticated users are sent to /login with the original target
 * preserved in the `redirect` query parameter.
 */
export async function authGuard(to: RouteLocationNormalized) {
  const auth = useAuthStore()
  const user = await auth.ensureLoaded()

  if (to.meta.public) {
    // A signed-in user has no business on the login page.
    if (to.name === 'login' && user) return { name: 'documents' }
    return true
  }
  if (!user) {
    return { name: 'login', query: { redirect: to.fullPath } }
  }
  // Admin-only routes are reserved for users holding the admin role; everyone
  // else is bounced back to the documents dashboard.
  if (to.meta.adminOnly && !user.is_admin) {
    return { name: 'documents' }
  }
  return true
}

const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes,
})

router.beforeEach(authGuard)

export default router

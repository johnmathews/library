import {
  createRouter,
  createWebHistory,
  type RouteLocationNormalized,
  type RouteRecordRaw,
} from 'vue-router'
import HomeView from '../views/HomeView.vue'
import { useAuthStore } from '@/stores/auth'

export const routes: RouteRecordRaw[] = [
  {
    path: '/',
    name: 'documents',
    component: HomeView,
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
  return true
}

const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes,
})

router.beforeEach(authGuard)

export default router

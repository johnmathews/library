import { fileURLToPath, URL } from 'node:url'

import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import vueDevTools from 'vite-plugin-vue-devtools'
import tailwindcss from '@tailwindcss/vite'

// Where the dev server and `vite preview` send `/api` and `/healthz`. Overridable
// because port 8000 is routinely occupied by a developer's own long-running
// `library-*` stack, and the alternative — stopping theirs to run the e2e suite —
// is worse than a variable. CI leaves it unset and gets the default.
//
// `preview` needs no proxy block of its own: it inherits `server.proxy`, checked
// by serving the build with and without one and comparing the `/healthz` body
// (not its status — the SPA fallback returns 200 for an unproxied path too, so a
// status-only check "passes" either way).
const apiTarget = process.env.LIBRARY_API_PROXY_TARGET ?? 'http://localhost:8000'

// https://vite.dev/config/
export default defineConfig({
  plugins: [
    vue(),
    vueDevTools(),
    tailwindcss(),
  ],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    proxy: {
      '/api': apiTarget,
      '/healthz': apiTarget,
    },
  },
})

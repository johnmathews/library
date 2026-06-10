import { fileURLToPath, URL } from 'node:url'

import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import vueDevTools from 'vite-plugin-vue-devtools'

// https://vite.dev/config/
export default defineConfig({
  plugins: [
    vue(),
    vueDevTools(),
  ],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url))
    },
  },
  css: {
    preprocessorOptions: {
      scss: {
        // Don't surface deprecation warnings from govuk-frontend itself.
        quietDeps: true,
      },
    },
    lightningcss: {
      // govuk-frontend ships an old-IE `(min-width: 0\0)` media-query hack
      // that LightningCSS refuses to parse; errorRecovery strips it (we do
      // not support any browser that needs it).
      errorRecovery: true,
    },
  },
  server: {
    proxy: {
      '/api': 'http://localhost:8000',
      '/healthz': 'http://localhost:8000',
    },
  },
})

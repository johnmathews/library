import { fileURLToPath } from 'node:url'
import { mergeConfig, defineConfig, configDefaults } from 'vitest/config'
import viteConfig from './vite.config'

export default mergeConfig(
  viteConfig,
  defineConfig({
    test: {
      environment: 'jsdom',
      exclude: [...configDefaults.exclude, 'e2e/**'],
      root: fileURLToPath(new URL('./', import.meta.url)),
      coverage: {
        provider: 'v8',
        // text → console; json-summary → CI table; html → downloadable artifact.
        reporter: ['text', 'json-summary', 'html'],
        reportsDirectory: './coverage',
        include: ['src/**/*.{ts,vue}'],
        exclude: [
          'src/**/__tests__/**',
          'src/**/*.spec.ts',
          'src/main.ts',
          'src/**/*.d.ts',
        ],
      },
    },
  }),
)

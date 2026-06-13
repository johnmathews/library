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
        // Coverage gate: `vitest run --coverage` exits non-zero below these.
        // Branches sit lower (every ||, ?., default param and error path
        // counts) so they carry a justified carve-out; the rest gate at 85%.
        thresholds: {
          lines: 85,
          statements: 85,
          functions: 85,
          branches: 75,
        },
      },
    },
  }),
)

import { defineConfig } from 'vitest/config';

// Vitest 設定。
//
// Cognito SDK は `window.crypto` / `localStorage` 等のブラウザ API に
// 依存するため、テスト環境は `jsdom` 固定とする。
//
// `globals: true` は意図的に無効化（明示 import を強制し、隠れた API
// 依存を発生させない方針）。
export default defineConfig({
  test: {
    environment: 'jsdom',
    globals: false,
    include: ['src/**/*.{test,spec}.{ts,tsx}'],
    setupFiles: ['./src/__tests__/setup.ts'],
    coverage: {
      provider: 'v8',
      reporter: ['text', 'html'],
      include: [
        'src/auth/**',
        'src/api/**',
        'src/routing/**',
        'src/employees/**',
        'src/cycles/**',
        'src/inbound/**',
        'src/dictionary/**',
      ],
    },
  },
});

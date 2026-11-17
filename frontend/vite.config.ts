import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { execFileSync } from 'node:child_process'

// Build-time git marker (FR-37 / v1.39): proves which checkout this dist was
// built from. The backend reports its own runtime hash; when the two differ
// the page warns that `npm run build` was forgotten after editing .ts/.tsx.
function gitShortHash(): string {
  try {
    const out = execFileSync('git', ['rev-parse', '--short', 'HEAD'], {
      encoding: 'utf-8',
      stdio: ['ignore', 'pipe', 'ignore'],
      timeout: 2000,
    })
    return String(out).trim() || 'unknown'
  } catch {
    return 'unknown'
  }
}

// dev server proxies API + WS to the FastAPI backend on :8000
export default defineConfig({
  define: { __BUILD_ID__: JSON.stringify(gitShortHash()) },
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': 'http://127.0.0.1:8000',
      '/ws': { target: 'ws://127.0.0.1:8000', ws: true },
    },
  },
  build: { outDir: 'dist' },
})

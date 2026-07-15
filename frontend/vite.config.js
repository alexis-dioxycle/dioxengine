import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import crypto from 'node:crypto'

// Local dev only: the vite dev server plays the portal's role by signing the
// identity headers (see backend/dioxycle_auth.py). Unused in the portal
// build, which only runs `npm run build`.
const DEV_USER = JSON.stringify({ id: '1', email: 'dev@dioxycle.com', name: 'Dev User', role: 'engineer' })
const DEV_SIG = crypto
  .createHmac('sha256', process.env.DIOXYCLE_AUTH_SECRET || 'test-secret')
  .update(DEV_USER)
  .digest('hex')

const withIdentity = {
  target: 'http://localhost:8000',
  configure(proxy) {
    proxy.on('proxyReq', (proxyReq) => {
      proxyReq.setHeader('X-Dioxycle-User', DEV_USER)
      proxyReq.setHeader('X-Dioxycle-Signature', DEV_SIG)
    })
  },
}

export default defineConfig({
  plugins: [react()],
  // Relative base so the bundle works at / (standalone) AND under /_apps/<slug>/ (portal proxy)
  base: './',
  build: { outDir: 'dist' },
  server: {
    port: 3001,
    proxy: { '/api': withIdentity, '/healthz': withIdentity },
  },
})

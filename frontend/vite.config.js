/* global process */
import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  // Local facility-service (backend/facility-service: `python scripts/local.py serve`).
  const backend = env.FACILITY_API_TARGET || 'http://localhost:8019'

  return {
    plugins: [react()],
    server: {
      port: 3000,
      // Same-origin /api calls keep the SameSite=Strict refresh cookie working in development.
      // The backend only accepts session requests from its own or allow-listed origins, so the
      // dev proxy presents the backend's own origin.
      proxy: {
        '/api': {
          target: backend,
          changeOrigin: true,
          configure: (proxy) => {
            proxy.on('proxyReq', (request) => {
              if (request.getHeader('origin')) request.setHeader('origin', backend)
              request.removeHeader('referer')
            })
          },
        },
      },
    },
    // Vitest: `npm test`. MSW stands in for the facility-service API (src/test/server.js).
    test: {
      environment: 'jsdom',
      setupFiles: ['./src/test/setup.js'],
      restoreMocks: true,
    },
  }
})

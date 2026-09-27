import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// The Core the dev server proxies to. CORE_URL runs a second copy side by side, e.g.
//   $env:CORE_URL = 'http://127.0.0.1:8100'; npm --prefix web run dev -- --port 5273
const core = process.env.CORE_URL ?? 'http://127.0.0.1:8000'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  // One .env for the whole repository: the page gets only the VITE_-prefixed values (the Google Maps
  // key and the trip's origin and destination), never the Core's secrets.
  envDir: '..',
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      // WebSocket to the Core server (core/, FastAPI)
      '/ws': {
        target: core.replace(/^http/, 'ws'),
        ws: true,
      },
      // Cached ElevenLabs audio named in PLAY_AUDIO
      '/audio': core,
      // REST: the calibrated head range (GET / PUT /api/head-range)
      '/api': core,
    },
  },
})

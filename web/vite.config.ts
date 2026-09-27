import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// The Core the dev server proxies to. CORE_URL runs a second copy side by side, e.g.
//   $env:CORE_URL = 'http://127.0.0.1:8100'; npm --prefix web run dev -- --port 5273
const core = process.env.CORE_URL ?? 'http://127.0.0.1:8000'

// Cross-origin isolation. Eyedid web (the browser eye tracker, src/facetrack/eyedidWeb.ts) runs a
// multithreaded WebAssembly engine, and browsers only allow threads (SharedArrayBuffer) on an isolated
// page. Everything the board loads is same-origin (the proxied Core, /mediapipe), and the engine's CDN
// sends Cross-Origin-Resource-Policy: cross-origin, so isolation breaks nothing here.
const isolation = {
  'Cross-Origin-Opener-Policy': 'same-origin',
  'Cross-Origin-Embedder-Policy': 'require-corp',
}

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  // The repo's .env (next to core/). Vite only hands VITE_* names to the page (VITE_EYEDID_WEB_KEY);
  // the Core's secrets in the same file never reach the browser.
  envDir: '..',
  preview: { headers: isolation },
  server: {
    headers: isolation,
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

import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { defineConfig } from 'vite'

export default defineConfig({
  define: { 'process.env.NODE_ENV': '"production"' },
  plugins: [react(), tailwindcss()],
  build: {
    outDir: 'dist/computer', emptyOutDir: true,
    lib: { entry: 'src/dev/computer.tsx', name: 'ClenchControls', formats: ['iife'], fileName: () => 'controls.js', cssFileName: 'controls' },
  },
})

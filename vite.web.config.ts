import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { resolve } from 'node:path'

const apiTarget = process.env.POLYKIT_API_URL?.trim() || 'http://127.0.0.1:8765'
const apiPrefixes = [
  '/health',
  '/system',
  '/model',
  '/generate',
  '/workflow-runs',
  '/workflow-definitions',
  '/workspace',
  '/workspace-library',
  '/optimize',
  '/node-packs',
  '/node_types',
  '/export',
  '/settings',
  '/agent',
  '/openapi.json',
  '/docs',
  '/redoc',
]

const apiProxy = Object.fromEntries(
  apiPrefixes.map((prefix) => [prefix, { target: apiTarget, changeOrigin: true }]),
)

export default defineConfig({
  root: resolve('src/web'),
  plugins: [react()],
  resolve: {
    alias: {
      '@': resolve('src'),
      '@areas': resolve('src/areas'),
      '@shared': resolve('src/shared'),
      '@styles': resolve('src/styles'),
    },
  },
  build: {
    outDir: resolve('dist-web'),
    emptyOutDir: true,
    rollupOptions: {
      output: {
        manualChunks: {
          react: ['react', 'react-dom'],
          three: ['three', 'three-mesh-bvh', '@react-three/fiber', '@react-three/drei', '@react-three/postprocessing'],
          flow: ['@xyflow/react'],
          splats: ['@mkkellogg/gaussian-splats-3d'],
          transport: ['axios', 'zustand'],
        },
      },
    },
  },
  server: {
    host: '0.0.0.0',
    port: 4175,
    proxy: apiProxy,
  },
  preview: {
    host: '0.0.0.0',
    port: 4173,
    proxy: apiProxy,
  },
})

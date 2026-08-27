import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { resolve } from 'node:path'
import { readFileSync } from 'node:fs'

const apiTarget = process.env.POLYKIT_API_URL?.trim() || 'http://127.0.0.1:8765'
const agentSourceRoot = resolve('agent/apps/web')
const apiPrefixes = [
  '/health',
  '/agent',
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
  '/openapi.json',
  '/docs',
  '/redoc',
]

const apiProxy = Object.fromEntries(
  apiPrefixes.map((prefix) => [prefix, { target: apiTarget, changeOrigin: true }]),
)

const agentStyles = {
  name: 'polykit-agent-chat-styles',
  enforce: 'pre' as const,
  load(id: string) {
    const sourcePath = resolve(agentSourceRoot, 'app/globals.css')
    if (id !== sourcePath) return null

    // Keep the chat-specific CSS from the migrated UI, but not its second
    // Tailwind pipeline or document-wide reset. PolyKit owns both globally.
    const source = readFileSync(sourcePath, 'utf8')
    const marker = '/* Context strip:'
    const start = source.indexOf(marker)
    return start >= 0 ? source.slice(start) : source
  },
}

export default defineConfig({
  root: resolve('src/web'),
  plugins: [react(), agentStyles],
  resolve: {
    // The copied Agent components retain their historical `@/hooks` and
    // `@/lib` imports. PolyKit itself uses scoped aliases (`@shared`,
    // `@areas`), so redirect only those legacy namespaces to the migration
    // source and leave the root `@/` alias available for future app code.
    alias: [
      { find: /^@\/(hooks|lib)\//, replacement: `${agentSourceRoot}/$1/` },
      { find: '@agent', replacement: agentSourceRoot },
      { find: '@', replacement: resolve('src') },
      { find: '@areas', replacement: resolve('src/areas') },
      { find: '@shared', replacement: resolve('src/shared') },
      { find: '@styles', replacement: resolve('src/styles') },
    ],
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

import { create } from 'zustand'
import { buildTerrain, type BuiltTerrain } from './runtime/terrain'
import { solvePlacements } from './runtime/placement'
import { isRenderableWorldSpec, type Instance } from './runtime/types'
import type { WorldDocument } from './types'

export interface WorldState {
  document: WorldDocument | null
  terrain: BuiltTerrain | null
  instances: Instance[]
  selectedProtoId: string | null
  saving: boolean
  error: string | null
  setSelectedProtoId: (protoId: string | null) => void
  replaceDocument: (document: WorldDocument) => void
  save: () => Promise<void>
  clearError: () => void
}

function now(): string {
  return new Date().toISOString()
}

function prepare(document: WorldDocument): { document: WorldDocument; terrain: BuiltTerrain | null; instances: Instance[] } {
  // Agent-created scene records are persisted before their plan is complete.
  // Never send an incomplete Agent-created scene into the terrain generator.
  if (!isRenderableWorldSpec(document.spec)) {
    return { document, terrain: null, instances: [] }
  }
  const terrain = buildTerrain(document.spec, { resolution: 96 })
  const instances = document.instances.length > 0 ? document.instances : solvePlacements(document.spec, terrain)
  return { document, terrain, instances }
}

export const useWorldStore = create<WorldState>((set, get) => ({
  document: null,
  terrain: null,
  instances: [],
  selectedProtoId: null,
  saving: false,
  error: null,

  setSelectedProtoId: (selectedProtoId) => set({ selectedProtoId }),
  replaceDocument: (document) => {
    const next = prepare(document)
    set({ ...next, error: null, selectedProtoId: null })
  },
  async save() {
    const current = get().document
    if (!current) return
    const { saveWorld } = await import('./worldApi')
    set({ saving: true, error: null })
    try {
      const document = { ...current, updated_at: now() }
      await saveWorld(document)
      set({ document, saving: false })
    } catch (error) {
      set({ saving: false, error: error instanceof Error ? error.message : String(error) })
    }
  },
  clearError: () => set({ error: null }),
}))

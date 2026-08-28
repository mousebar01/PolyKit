import { create } from 'zustand'
import { DEMO_SPEC } from './runtime/demo'
import { buildTerrain, type BuiltTerrain } from './runtime/terrain'
import { solvePlacements } from './runtime/placement'
import type { Instance, WorldSpec } from './runtime/types'
import type { WorldAssetArtifact, WorldDocument } from './types'
import { createWorldAgentPlan } from './worldPlan'

const DEMO_ID = 'emberfall-reach'

export interface WorldState {
  document: WorldDocument
  terrain: BuiltTerrain
  instances: Instance[]
  selectedProtoId: string | null
  saving: boolean
  loading: boolean
  error: string | null
  setSelectedProtoId: (protoId: string | null) => void
  replaceDocument: (document: WorldDocument) => void
  updateInstances: (instances: Instance[]) => void
  updateArtifact: (protoId: string, artifact: WorldAssetArtifact) => void
  save: () => Promise<void>
  load: (worldId: string) => Promise<void>
  clearError: () => void
}

function now(): string {
  return new Date().toISOString()
}

export function createWorldDocument(spec: WorldSpec = DEMO_SPEC, id = DEMO_ID): WorldDocument {
  const timestamp = now()
  const terrain = buildTerrain(spec, { resolution: 96 })
  const instances = solvePlacements(spec, terrain)
  const artifacts = Object.fromEntries(
    spec.assets.map((asset) => [asset.id, { mode: 'procedural' as const }]),
  )
  return {
    schema_version: 1,
    kind: 'polykit.world',
    id,
    name: spec.name,
    created_at: timestamp,
    updated_at: timestamp,
    spec,
    instances,
    artifacts,
    agent_plan: createWorldAgentPlan(),
  }
}

function prepare(document: WorldDocument): { document: WorldDocument; terrain: BuiltTerrain; instances: Instance[] } {
  const terrain = buildTerrain(document.spec, { resolution: 96 })
  const instances = document.instances.length > 0 ? document.instances : solvePlacements(document.spec, terrain)
  return { document, terrain, instances }
}

const initial = createWorldDocument()

export const useWorldStore = create<WorldState>((set, get) => ({
  document: initial,
  terrain: buildTerrain(initial.spec, { resolution: 96 }),
  instances: initial.instances,
  selectedProtoId: null,
  saving: false,
  loading: false,
  error: null,

  setSelectedProtoId: (selectedProtoId) => set({ selectedProtoId }),
  replaceDocument: (document) => {
    const next = prepare(document)
    set({ ...next, error: null, selectedProtoId: null })
  },
  updateInstances: (instances) => set((state) => ({
    instances,
    document: { ...state.document, instances, updated_at: now() },
  })),
  updateArtifact: (protoId, artifact) => set((state) => ({
    document: {
      ...state.document,
      updated_at: now(),
      artifacts: { ...state.document.artifacts, [protoId]: artifact },
    },
  })),
  async save() {
    const { saveWorld } = await import('./worldApi')
    set({ saving: true, error: null })
    try {
      const document = { ...get().document, updated_at: now() }
      await saveWorld(document)
      set({ document, saving: false })
    } catch (error) {
      set({ saving: false, error: error instanceof Error ? error.message : String(error) })
    }
  },
  async load(worldId) {
    const { loadWorld } = await import('./worldApi')
    set({ loading: true, error: null })
    try {
      const document = await loadWorld(worldId)
      const next = prepare(document)
      set({ ...next, loading: false, selectedProtoId: null })
    } catch (error) {
      set({ loading: false, error: error instanceof Error ? error.message : String(error) })
    }
  },
  clearError: () => set({ error: null }),
}))

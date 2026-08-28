import type { Instance, WorldSpec } from './runtime/types'

export interface WorldMeshArtifact {
  kind: 'mesh'
  workspace_path: string
  workflow_id?: string
  run_id?: string
}

export interface WorldAssetArtifact {
  mode: 'procedural' | 'workspace-mesh'
  concept_image?: string
  mesh?: WorldMeshArtifact
}

/** Editable, server-owned world document. Derived terrain is never persisted. */
export interface WorldDocument {
  schema_version: 1
  kind: 'polykit.world'
  id: string
  name: string
  created_at: string
  updated_at: string
  spec: WorldSpec
  instances: Instance[]
  artifacts: Record<string, WorldAssetArtifact>
}

export interface WorldSaveResponse {
  world_id: string
  workspace_path: string
  url: string
}

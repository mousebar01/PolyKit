import type { Instance, WorldSpec } from './runtime/types'
import type { WorldAgentPlan } from './worldPlan'

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
  /** Server run that created this scene, when it came from Agent generation. */
  run_id?: string
  /** Optional parent scene when the Agent deliberately creates a revision. */
  parent_world_id?: string
  spec: WorldSpec
  instances: Instance[]
  artifacts: Record<string, WorldAssetArtifact>
  agent_plan?: WorldAgentPlan
}

export interface WorldSaveResponse {
  world_id: string
  workspace_path: string
  url: string
}

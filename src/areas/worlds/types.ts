import type { WorldRuntime } from './runtime/runtime'

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

/**
 * Server-owned editable world document.
 *
 * Schema v2 has one runtime contract.  Build, semantic scene, compiled output,
 * gameplay and workflow progress are no longer mirrored as top-level fields.
 */
export interface WorldDocument {
  schema_version: 2
  kind: 'polykit.world'
  id: string
  name: string
  created_at: string
  updated_at: string
  run_id?: string
  parent_world_id?: string
  runtime: WorldRuntime
  artifacts: Record<string, WorldAssetArtifact>
}

export interface WorldSaveResponse {
  world_id: string
  workspace_path: string
  url: string
}

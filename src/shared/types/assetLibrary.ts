import type { ArtifactProvenance } from './artifacts'

export const ASSET_CAPABILITIES = [
  'mesh',
  'rigged-mesh',
  'animation-motion',
  'landmarks-sidecar',
  'generated-world',
  'scene-manifest',
] as const

export const ASSET_ENTRY_STATES = ['ready', 'unknown-metadata', 'unsupported', 'unsafe'] as const
export const ASSET_LIBRARY_PREVIEW_KINDS = ['3d-model', 'text', 'binary', 'none'] as const
export const ASSET_LIBRARY_MANIFEST_CAPABILITIES = ['generated-world', 'scene-manifest'] as const
export const ASSET_LIBRARY_SOURCE_SCOPES = ['workflows', 'exports'] as const

export type AssetCapability = typeof ASSET_CAPABILITIES[number]
export type AssetEntryState = typeof ASSET_ENTRY_STATES[number]
export type AssetLibraryPreviewKind = typeof ASSET_LIBRARY_PREVIEW_KINDS[number]
export type AssetLibraryManifestCapability = typeof ASSET_LIBRARY_MANIFEST_CAPABILITIES[number]
export type AssetLibrarySourceScope = typeof ASSET_LIBRARY_SOURCE_SCOPES[number]

export interface AssetLibraryError {
  code: 'unsafe-path' | 'not-found' | 'not-openable' | 'read-failed' | 'list-failed' | 'invalid-request'
  message: string
}

export interface AssetLibraryDeleteRequest {
  workspacePaths: string[]
}

export interface AssetLibraryDeleteResult {
  success: boolean
  deleted: string[]
  missing: string[]
  rejected: string[]
}

export interface AssetLibraryRenameRequest {
  workspacePath: string
  newName: string
}

export interface AssetLibraryRenameResult {
  success: boolean
  workspacePath: string
  displayName: string
}

export interface AssetLibraryEntry {
  id: string
  workspacePath: string
  displayName: string
  /** Workspace collection that owns the file. */
  sourceScope?: AssetLibrarySourceScope
  capability?: AssetCapability
  state: AssetEntryState
  previewKind: AssetLibraryPreviewKind
  source?: AssetLibrarySourceLink
  manifest?: AssetLibraryManifestRef
  artifactId?: string
  versionId?: string
  provenance?: ArtifactProvenance
  warnings: string[]
  openable: boolean
  nonOpenableReason?: string
  /** Relative URL of a rendered preview PNG (meshes); undefined for others. */
  thumbnail?: string
  /** Relative URL of a lightweight textured GLB for client-side 3D preview (glb/gltf only). */
  preview?: string
  createdAt?: string
  updatedAt?: string
}

export interface AssetLibrarySourceLink {
  workspacePath: string
  displayName?: string
  role?: 'source-mesh' | 'source-artifact' | 'related-source'
}

export interface AssetLibraryManifestRef {
  workspacePath: string
  capability: AssetLibraryManifestCapability
}

export type AssetLibraryPreviewPayload =
  | { kind: '3d-model', viewerKind: 'glb' | 'gltf' }
  | { kind: 'text', content: string, byteLength: number, truncated: boolean }
  | { kind: 'binary', binaryKind: string, byteLength: number, message: string }
  | { kind: 'none' }

export interface AssetLibraryReadRequest {
  workspacePath: string
  sourceWorkspacePath?: string
}

export type AssetLibraryOpenRequest = AssetLibraryReadRequest
export type AssetLibraryListResult =
  | { success: true, entries: AssetLibraryEntry[] }
  | { success: false, error: AssetLibraryError }

export type AssetLibraryReadResult =
  | { success: true, entry: AssetLibraryEntry, preview: AssetLibraryPreviewPayload }
  | { success: false, error: AssetLibraryError }

export type AssetLibraryOpenResult =
  | { success: true, entry: AssetLibraryEntry }
  | { success: false, error: AssetLibraryError }

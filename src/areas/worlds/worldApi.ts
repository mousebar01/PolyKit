import axios from 'axios'
import { useAppStore } from '@shared/stores/appStore'
import type { WorldDocument, WorldSaveResponse } from './types'

export interface WorldSummary {
  id: string
  name: string
  updatedAt: string
  workspacePath: string
}

function client() {
  return axios.create({ baseURL: useAppStore.getState().apiUrl.replace(/\/+$/, '') })
}

export async function saveWorld(document: WorldDocument): Promise<WorldSaveResponse> {
  const { data } = await client().put<WorldSaveResponse>(
    `/workspace-library/worlds/${encodeURIComponent(document.id)}`,
    document,
  )
  return data
}

export async function loadWorld(worldId: string): Promise<WorldDocument> {
  const { data } = await client().get<WorldDocument>(
    `/workspace-library/worlds/${encodeURIComponent(worldId.trim())}`,
  )
  return data
}

/**
 * Return the saved scene records from the server-owned workspace library.
 * The library already filters unsafe/unsupported files, so the world preview
 * can discover a newly-created scene without owning another persistence path.
 */
export async function listWorlds(): Promise<WorldSummary[]> {
  const { data } = await client().get<{
    success?: boolean
    entries?: Array<{
      workspacePath?: string
      displayName?: string
      updatedAt?: string
    }>
  }>('/workspace-library/list')
  if (!data.success || !Array.isArray(data.entries)) return []
  return data.entries
    .filter((entry) => typeof entry.workspacePath === 'string' && entry.workspacePath.endsWith('.world.json'))
    .map((entry) => {
      const workspacePath = entry.workspacePath!
      const filename = workspacePath.split('/').pop() ?? workspacePath
      const id = filename.endsWith('.world.json') ? filename.slice(0, -'.world.json'.length) : filename
      return {
        id,
        name: entry.displayName?.replace(/\.world\.json$/, '') || id,
        updatedAt: entry.updatedAt ?? '',
        workspacePath,
      }
    })
    .sort((left, right) => right.updatedAt.localeCompare(left.updatedAt))
}

export function workspaceUrl(workspacePath: string): string {
  const base = useAppStore.getState().apiUrl.replace(/\/+$/, '')
  return `${base}/workspace/${workspacePath.replace(/^\/+/, '')}`
}


export interface WorldAssetResolutionDecision {
  object_id: string
  mode: 'existing' | 'procedural' | 'library' | 'generate' | 'unresolved'
  workspace_path?: string
  source?: string
  prompt?: string
  size?: [number, number, number]
  procedural_hint?: string
  reason?: string
}

export interface WorldAssetResolutionRun {
  run_id: string
  proto_id?: string
  status: 'pending'
  queued_nodes: number
}

export async function resolveWorldAssets(
  worldId: string,
  options: {
    generateMissing?: boolean
    includeContext?: boolean
    includeScatter?: boolean
    minLibraryScore?: number
    collection?: string
    enableTexture?: boolean
    enableOptimize?: boolean
    targetFaces?: number
  } = {},
): Promise<{
  world_id: string
  decisions: WorldAssetResolutionDecision[]
  generation_runs: WorldAssetResolutionRun[]
  world: WorldDocument
}> {
  const { data } = await client().post(
    `/workspace-library/worlds/${encodeURIComponent(worldId.trim())}/resolve-assets`,
    {
      generate_missing: options.generateMissing ?? true,
      include_context: options.includeContext ?? false,
      include_scatter: options.includeScatter ?? false,
      min_library_score: options.minLibraryScore ?? 3,
      collection: options.collection ?? 'WorldAssets',
      enable_texture: options.enableTexture ?? true,
      enable_optimize: options.enableOptimize ?? true,
      target_faces: options.targetFaces ?? 100_000,
    },
  )
  return data
}


export interface WorldResolutionRunStatus {
  run_id: string
  status: string
  progress: number
  step?: string
  error?: string
}

export async function getWorldResolutionRunStatus(runId: string): Promise<WorldResolutionRunStatus> {
  const { data } = await client().get<WorldResolutionRunStatus>(
    `/runs/${encodeURIComponent(runId)}?compact=true`,
  )
  return data
}

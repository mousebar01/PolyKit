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
 * The library already filters unsafe/unsupported files, so the Agent preview
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

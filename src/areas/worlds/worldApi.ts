import axios from 'axios'
import { useAppStore } from '@shared/stores/appStore'
import type { WorldDocument, WorldSaveResponse } from './types'

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

export function workspaceUrl(workspacePath: string): string {
  const base = useAppStore.getState().apiUrl.replace(/\/+$/, '')
  return `${base}/workspace/${workspacePath.replace(/^\/+/, '')}`
}

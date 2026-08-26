import type {
  AssetLibraryDeleteRequest,
  AssetLibraryDeleteResult,
  AssetLibraryError,
  AssetLibraryListResult,
  AssetLibraryOpenRequest,
  AssetLibraryOpenResult,
  AssetLibraryReadRequest,
  AssetLibraryReadResult,
  AssetLibraryRenameRequest,
  AssetLibraryRenameResult,
} from '../../shared/types/assetLibrary'
import { projectAssetLibraryEntry, type ProjectedAssetLibraryEntry } from './assetLibraryProjection'

export interface AssetLibraryPreloadApi {
  list: () => Promise<AssetLibraryListResult>
  read: (request: AssetLibraryReadRequest) => Promise<AssetLibraryReadResult>
  open: (request: AssetLibraryOpenRequest) => Promise<AssetLibraryOpenResult>
  delete: (request: AssetLibraryDeleteRequest) => Promise<AssetLibraryDeleteResult>
  rename: (request: AssetLibraryRenameRequest) => Promise<AssetLibraryRenameResult>
}

export type ProjectedAssetLibraryDeleteResult = AssetLibraryDeleteResult
export type ProjectedAssetLibraryRenameResult = AssetLibraryRenameResult

export type ProjectedAssetLibraryListResult =
  | { success: true, entries: ProjectedAssetLibraryEntry[] }
  | { success: false, error: AssetLibraryError }

export type ProjectedAssetLibraryReadResult =
  | { success: true, entry: ProjectedAssetLibraryEntry, preview: Extract<AssetLibraryReadResult, { success: true }>['preview'] }
  | { success: false, error: AssetLibraryError }

export type ProjectedAssetLibraryOpenResult =
  | { success: true, entry: ProjectedAssetLibraryEntry }
  | { success: false, error: AssetLibraryError }

function unsafeError(message = 'Workspace path must stay under Workflows/.'): AssetLibraryError {
  return { code: 'unsafe-path', message }
}

function validateWorkspacePath(workspacePath: string): AssetLibraryError | null {
  const trimmed = workspacePath.trim()
  if (!trimmed || trimmed.startsWith('/') || /^[a-zA-Z]:[\\/]/.test(trimmed) || trimmed.startsWith('\\\\')) return unsafeError()
  if (/%2e|%2f|%5c/i.test(trimmed) || trimmed.split(/[\\/]+/).includes('..')) return unsafeError()
  const normalized = trimmed.replace(/\\/g, '/')
  if (!normalized.startsWith('Workflows/')) return unsafeError()
  return null
}

export function createAssetLibraryService(api: AssetLibraryPreloadApi) {
  return {
    async list(): Promise<ProjectedAssetLibraryListResult> {
      const result = await api.list()
      if (!result.success) return result
      return { success: true, entries: result.entries.map(projectAssetLibraryEntry) }
    },
    async read(request: AssetLibraryReadRequest): Promise<ProjectedAssetLibraryReadResult> {
      const invalid = validateWorkspacePath(request.workspacePath) ?? (request.sourceWorkspacePath ? validateWorkspacePath(request.sourceWorkspacePath) : null)
      if (invalid) return { success: false, error: invalid }
      const result = await api.read(request)
      if (!result.success) return result
      return { success: true, entry: projectAssetLibraryEntry(result.entry), preview: result.preview }
    },
    async open(request: AssetLibraryOpenRequest): Promise<ProjectedAssetLibraryOpenResult> {
      const invalid = validateWorkspacePath(request.workspacePath) ?? (request.sourceWorkspacePath ? validateWorkspacePath(request.sourceWorkspacePath) : null)
      if (invalid) return { success: false, error: invalid }
      const result = await api.open(request)
      if (!result.success) return result
      return { success: true, entry: projectAssetLibraryEntry(result.entry) }
    },
    async delete(request: AssetLibraryDeleteRequest): Promise<ProjectedAssetLibraryDeleteResult> {
      const invalid = request.workspacePaths.find((path) => validateWorkspacePath(path) !== null)
      if (invalid) return { success: false, deleted: [], missing: [], rejected: [invalid] }
      return api.delete(request)
    },
    async rename(request: AssetLibraryRenameRequest): Promise<ProjectedAssetLibraryRenameResult> {
      const invalid = validateWorkspacePath(request.workspacePath)
      if (invalid) return { success: false, workspacePath: request.workspacePath, displayName: request.newName }
      return api.rename(request)
    },
  }
}

export function getDefaultAssetLibraryService() {
  return createAssetLibraryService(window.polykit.workspace.library)
}

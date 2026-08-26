import type { AssetLibraryEntry } from '../../shared/types/assetLibrary'

export interface ProjectedAssetLibraryEntry extends AssetLibraryEntry {
  warnings: string[]
}

export type AssetLibraryOpenTarget =
  | { kind: 'self', url: string, workspacePath: string }
  | { kind: 'linked-source', url: string, workspacePath: string, sourceWorkspacePath: string }
  | { kind: 'unavailable', reason: string }

function isSafeWorkspacePath(workspacePath: string): boolean {
  const normalized = workspacePath.replace(/\\/g, '/').trim()
  return /^Workflows\//.test(normalized)
    && !normalized.split('/').includes('..')
    && !/%2e|%2f|%5c/i.test(normalized)
    && !normalized.startsWith('/')
    && !/^[a-zA-Z]:[\\/]/.test(workspacePath)
    && !workspacePath.startsWith('\\\\')
}

function isGlbOrGltf(workspacePath: string): boolean {
  return /\.(glb|gltf)$/i.test(workspacePath)
}

export function projectAssetLibraryEntry(entry: AssetLibraryEntry): ProjectedAssetLibraryEntry {
  const warnings = [...new Set(entry.warnings)]
  if (!isSafeWorkspacePath(entry.workspacePath)) {
    return { ...entry, state: 'unsafe', openable: false, nonOpenableReason: entry.nonOpenableReason ?? 'Unsafe workspace path.', warnings }
  }
  const safeSource = entry.source && isSafeWorkspacePath(entry.source.workspacePath) ? entry.source : undefined
  const safeManifest = entry.manifest && isSafeWorkspacePath(entry.manifest.workspacePath) ? entry.manifest : undefined
  if (entry.source && !safeSource) warnings.push('Ignored unsafe source workspace path.')
  if (entry.manifest && !safeManifest) warnings.push('Ignored unsafe manifest workspace path.')
  return { ...entry, source: safeSource, manifest: safeManifest, warnings: [...new Set(warnings)] }
}

export function resolveAssetLibraryOpenTarget(entry: ProjectedAssetLibraryEntry): AssetLibraryOpenTarget {
  if (entry.state !== 'ready') {
    return { kind: 'unavailable', reason: entry.nonOpenableReason ?? 'Workspace asset is not openable.' }
  }
  if (entry.source?.workspacePath) {
    if (!isSafeWorkspacePath(entry.source.workspacePath) || entry.source.workspacePath === entry.workspacePath || !isGlbOrGltf(entry.source.workspacePath)) {
      return { kind: 'unavailable', reason: 'Linked source mesh is unavailable.' }
    }
    return {
      kind: 'linked-source',
      url: `/workspace/${entry.source.workspacePath}`,
      workspacePath: entry.workspacePath,
      sourceWorkspacePath: entry.source.workspacePath,
    }
  }
  if (!entry.openable) {
    return { kind: 'unavailable', reason: entry.nonOpenableReason ?? 'Workspace asset is not openable.' }
  }
  if (!isGlbOrGltf(entry.workspacePath)) {
    return { kind: 'unavailable', reason: 'Only safe .glb/.gltf workspace assets are openable in this release.' }
  }
  return { kind: 'self', url: `/workspace/${entry.workspacePath}`, workspacePath: entry.workspacePath }
}

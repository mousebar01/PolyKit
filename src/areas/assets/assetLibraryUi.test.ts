import assert from 'node:assert/strict'
import test from 'node:test'

import {
  buildAssetLibraryOpenRequest,
  createAssetLibraryOpenJob,
  DEFAULT_ASSET_LIBRARY_SORT_MODE,
  describeAssetLibraryOpenability,
  filterAssetLibraryEntryGroups,
  getDefaultAssetLibraryCollapsedSectionKeys,
  isAssetLibraryEntryOpenable,
  toggleAssetLibrarySectionKey,
  type AssetLibrarySortMode,
} from './assetLibraryUi.ts'
import { projectAssetLibraryEntry, resolveAssetLibraryOpenTarget, type ProjectedAssetLibraryEntry } from './assetLibraryProjection.ts'
import type { AssetLibraryEntry } from '../../shared/types/assetLibrary.ts'

function entry(overrides: Partial<AssetLibraryEntry> = {}): ProjectedAssetLibraryEntry {
  const base: AssetLibraryEntry = {
    id: 'library:Workflows/hero.glb',
    workspacePath: 'Workflows/hero.glb',
    displayName: 'hero.glb',
    capability: 'mesh',
    state: 'ready',
    previewKind: '3d-model',
    warnings: [],
    openable: true,
    createdAt: '2026-06-16T10:00:00.000Z',
    ...overrides,
  }
  return projectAssetLibraryEntry(base)
}

function groupPaths(entries: ProjectedAssetLibraryEntry[], search = '', sortMode: AssetLibrarySortMode = 'date'): string[] {
  return filterAssetLibraryEntryGroups(entries, search, sortMode).flatMap((group) => (
    group.entries.map((item) => item.workspacePath)
  ))
}

test('organizes visible library assets by capability with primary media and scenes expanded by default', () => {
  const entries = [
    entry({ id: 'image', workspacePath: 'Workflows/Illustrations/hero.png', displayName: 'hero.png', capability: 'image', previewKind: 'image' }),
    entry({ id: 'workflow-mesh', workspacePath: 'Workflows/run/hero.glb', displayName: 'hero.glb', capability: 'mesh' }),
    entry({ id: 'rig', workspacePath: 'Workflows/rig/hero-rig.gltf', displayName: 'hero-rig.gltf', capability: 'rigged-mesh' }),
    entry({ id: 'hidden-cache', workspacePath: 'Workflows/run/cache/internal.glb', displayName: 'internal.glb' }),
    entry({ id: 'unsupported', workspacePath: 'Workflows/readme.txt', displayName: 'readme.txt', state: 'unsupported', openable: false }),
  ]

  const groups = filterAssetLibraryEntryGroups(entries, '', 'date')
  assert.deepEqual(groups.map((group) => group.capability), ['image', 'mesh', 'rigged-mesh'])
  assert.deepEqual(groupPaths(entries), ['Workflows/Illustrations/hero.png', 'Workflows/run/hero.glb', 'Workflows/rig/hero-rig.gltf'])
  assert.equal(DEFAULT_ASSET_LIBRARY_SORT_MODE, 'date')
  assert.equal(getDefaultAssetLibraryCollapsedSectionKeys().includes('capability:mesh'), false)
  assert.equal(getDefaultAssetLibraryCollapsedSectionKeys().includes('capability:image'), false)
  assert.equal(getDefaultAssetLibraryCollapsedSectionKeys().includes('capability:generated-world'), false)
  assert.equal(getDefaultAssetLibraryCollapsedSectionKeys().includes('capability:rigged-mesh'), true)
  assert.deepEqual(toggleAssetLibrarySectionKey([], 'capability:mesh'), ['capability:mesh'])
  assert.deepEqual(toggleAssetLibrarySectionKey(['capability:rigged-mesh'], 'capability:rigged-mesh'), [])
})

test('searches workspace assets while date sorting keeps capability groups stable and newest mesh first', () => {
  const entries = [
    entry({
      id: 'b',
      workspacePath: 'Workflows/run/zebra.glb',
      displayName: 'zebra.glb',
      capability: 'mesh',
      createdAt: '2026-06-15T10:00:00.000Z',
      updatedAt: '2026-06-18T10:00:00.000Z',
    }),
    entry({
      id: 'd',
      workspacePath: 'Workflows/run/beta.glb',
      displayName: 'beta.glb',
      capability: 'mesh',
      createdAt: '2026-06-17T10:00:00.000Z',
    }),
    entry({ id: 'a', workspacePath: 'Workflows/rig/alpha.gltf', displayName: 'alpha.gltf', capability: 'rigged-mesh', createdAt: '2026-06-16T10:00:00.000Z' }),
    entry({
      id: 'c', workspacePath: 'Workflows/motion/walk.json', displayName: 'walk.json', capability: 'animation-motion', openable: false, previewKind: 'text',
      source: { workspacePath: 'Workflows/run/zebra.glb', displayName: 'zebra.glb' },
      manifest: { workspacePath: 'Workflows/motion/walk.scene.json', capability: 'scene-manifest' },
    }),
  ]

  assert.deepEqual(groupPaths(entries, 'rigged'), ['Workflows/rig/alpha.gltf'])
  assert.deepEqual(groupPaths(entries, 'walk.scene'), ['Workflows/motion/walk.json'])
  assert.deepEqual(groupPaths(entries, 'zebra.glb'), ['Workflows/run/zebra.glb', 'Workflows/motion/walk.json'])
  assert.deepEqual(groupPaths(entries, 'motion'), ['Workflows/motion/walk.json'])
  assert.deepEqual(groupPaths(entries, '', 'date'), [
    'Workflows/run/zebra.glb',
    'Workflows/run/beta.glb',
    'Workflows/rig/alpha.gltf',
    'Workflows/motion/walk.json',
  ])
})

test('opens safe images, glb, and gltf entries through the shared viewer job state', () => {
  const glb = entry({ workspacePath: 'Workflows/run/hero.glb', displayName: 'hero.glb' })
  const image = entry({ id: 'image', workspacePath: 'Workflows/Illustrations/hero.png', displayName: 'hero.png', capability: 'image', previewKind: 'image' })
  const ply = entry({ workspacePath: 'Workflows/scan.ply', displayName: 'scan.ply', openable: false, nonOpenableReason: 'Only .glb/.gltf workspace assets are openable in this release.' })

  assert.equal(isAssetLibraryEntryOpenable(glb), true)
  assert.equal(isAssetLibraryEntryOpenable(image), true)
  assert.equal(isAssetLibraryEntryOpenable(ply), false)
  assert.equal(describeAssetLibraryOpenability(glb), 'Ready to open this asset directly in Generate.')
  assert.equal(describeAssetLibraryOpenability(ply), 'Only .glb/.gltf workspace assets are openable in this release.')
  assert.deepEqual(buildAssetLibraryOpenRequest(glb), { workspacePath: 'Workflows/run/hero.glb' })

  const target = resolveAssetLibraryOpenTarget(glb)
  assert.equal(target.kind, 'self')
  if (target.kind !== 'self') throw new Error('expected self target')

  const selection = createAssetLibraryOpenJob(target, 1718546400000)
  assert.ok(selection)
  assert.equal(selection.historyUrl, '/workspace/Workflows/run/hero.glb')
  assert.equal(selection.job.status, 'done')
  assert.equal(selection.job.outputUrl, '/workspace/Workflows/run/hero.glb')
  assert.equal(selection.job.originalOutputUrl, '/workspace/Workflows/run/hero.glb')
  const imageSelection = createAssetLibraryOpenJob(resolveAssetLibraryOpenTarget(image), 1718546400003)
  assert.equal(imageSelection?.job.outputKind, 'image')
})

test('keeps generated worlds openable without pretending they are mesh jobs', () => {
  const scene = entry({
    id: 'scene',
    workspacePath: 'Workflows/emberfall.world.json',
    displayName: 'emberfall.world.json',
    capability: 'generated-world',
    previewKind: 'text',
  })
  assert.equal(isAssetLibraryEntryOpenable(scene), true)
  assert.equal(describeAssetLibraryOpenability(scene), 'Ready to open this generated scene in the viewer.')
  assert.deepEqual(buildAssetLibraryOpenRequest(scene), { workspacePath: 'Workflows/emberfall.world.json' })
  assert.equal(createAssetLibraryOpenJob(resolveAssetLibraryOpenTarget(scene)), null)
})

test('keeps library jobs pointed at the full server asset', () => {
  const glb = entry({
    preview: '/workspace-library/preview?path=Workflows%2Frun%2Fhero.glb',
    thumbnail: '/workspace-library/thumbnail?path=Workflows%2Frun%2Fhero.glb',
  })
  const target = resolveAssetLibraryOpenTarget(glb)
  if (target.kind !== 'self') throw new Error('expected self target')
  const selection = createAssetLibraryOpenJob(target, 1718546400002)
  assert.equal(selection?.job.outputUrl, '/workspace/Workflows/hero.glb')
})

test('builds linked-source open requests and import jobs for safe sidecars', () => {
  const sidecar = entry({
    id: 'sidecar',
    workspacePath: 'Workflows/run/hero.landmarks.v1.json',
    displayName: 'hero.landmarks.v1.json',
    capability: 'landmarks-sidecar',
    previewKind: 'text',
    openable: false,
    source: { workspacePath: 'Workflows/run/hero.glb', displayName: 'hero.glb' },
  })

  assert.equal(isAssetLibraryEntryOpenable(sidecar), true)
  assert.equal(describeAssetLibraryOpenability(sidecar), 'Ready to open linked source hero.glb in Generate.')
  assert.deepEqual(buildAssetLibraryOpenRequest(sidecar), {
    workspacePath: 'Workflows/run/hero.landmarks.v1.json',
    sourceWorkspacePath: 'Workflows/run/hero.glb',
  })

  const target = resolveAssetLibraryOpenTarget(sidecar)
  assert.equal(target.kind, 'linked-source')
  if (target.kind !== 'linked-source') throw new Error('expected linked source target')
  const selection = createAssetLibraryOpenJob(target, 1718546400001)
  assert.equal(selection?.historyUrl, '/workspace/Workflows/run/hero.glb')
  assert.equal(selection?.job.outputUrl, '/workspace/Workflows/run/hero.glb')
})

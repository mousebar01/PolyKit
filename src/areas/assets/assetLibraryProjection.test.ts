import assert from 'node:assert/strict'
import test from 'node:test'

import {
  projectAssetLibraryEntry,
  resolveAssetLibraryOpenTarget,
} from './assetLibraryProjection.ts'
import type { AssetLibraryEntry } from '../../shared/types/assetLibrary.ts'

const glbEntry: AssetLibraryEntry = {
  id: 'library:Workflows/checkpoints/hero.glb',
  workspacePath: 'Workflows/checkpoints/hero.glb',
  displayName: 'hero.glb',
  capability: 'mesh',
  state: 'ready',
  previewKind: '3d-model',
  warnings: ['safe', 'safe'],
  openable: true,
}

test('projects entries with deduped warnings and workspace URL open target', () => {
  const projected = projectAssetLibraryEntry(glbEntry)
  assert.deepEqual(projected.warnings, ['safe'])
  assert.deepEqual(resolveAssetLibraryOpenTarget(projected), {
    kind: 'self',
    url: '/workspace/Workflows/checkpoints/hero.glb',
    workspacePath: 'Workflows/checkpoints/hero.glb',
    assetKind: 'mesh',
  })
})

test('projects image assets to the image viewer target', () => {
  const projected = projectAssetLibraryEntry({
    ...glbEntry,
    id: 'library:Workflows/Illustrations/hero.png',
    workspacePath: 'Workflows/Illustrations/hero.png',
    displayName: 'hero.png',
    capability: 'image',
    previewKind: 'image',
  })

  assert.deepEqual(resolveAssetLibraryOpenTarget(projected), {
    kind: 'self',
    url: '/workspace/Workflows/Illustrations/hero.png',
    workspacePath: 'Workflows/Illustrations/hero.png',
    assetKind: 'image',
  })
})

test('projects sidecars with safe GLB source links as linked-source open targets', () => {
  const projected = projectAssetLibraryEntry({
    ...glbEntry,
    id: 'library:Workflows/checkpoints/hero.landmarks.v1.json',
    workspacePath: 'Workflows/checkpoints/hero.landmarks.v1.json',
    displayName: 'hero.landmarks.v1.json',
    capability: 'landmarks-sidecar',
    previewKind: 'text',
    openable: false,
    nonOpenableReason: 'Open the linked source mesh.',
    source: { workspacePath: 'Workflows/checkpoints/hero.glb', displayName: 'hero.glb', role: 'source-mesh' },
    manifest: { workspacePath: 'Workflows/checkpoints/hero.scene.json', capability: 'scene-manifest' },
  })

  assert.deepEqual(projected.source, { workspacePath: 'Workflows/checkpoints/hero.glb', displayName: 'hero.glb', role: 'source-mesh' })
  assert.deepEqual(projected.manifest, { workspacePath: 'Workflows/checkpoints/hero.scene.json', capability: 'scene-manifest' })
  assert.deepEqual(resolveAssetLibraryOpenTarget(projected), {
    kind: 'linked-source',
    workspacePath: 'Workflows/checkpoints/hero.landmarks.v1.json',
    sourceWorkspacePath: 'Workflows/checkpoints/hero.glb',
    url: '/workspace/Workflows/checkpoints/hero.glb',
    assetKind: 'mesh',
  })
})

test('degrades unsafe source and manifest paths and unsupported formats to unavailable targets', () => {
  const projected = projectAssetLibraryEntry({
    ...glbEntry,
    id: 'unsafe-source',
    workspacePath: 'Workflows/checkpoints/hero.landmarks.v1.json',
    displayName: 'hero.landmarks.v1.json',
    capability: 'landmarks-sidecar',
    previewKind: 'text',
    openable: false,
    source: { workspacePath: 'Workflows/%2e%2e/secret.glb' },
    manifest: { workspacePath: '../secret.scene.json', capability: 'scene-manifest' },
  })
  const splat = projectAssetLibraryEntry({ ...glbEntry, id: 'splat', workspacePath: 'Workflows/scan.splat', displayName: 'scan.splat', openable: true })

  assert.equal(projected.source, undefined)
  assert.equal(projected.manifest, undefined)
  assert.deepEqual(projected.warnings, ['safe', 'Ignored unsafe source workspace path.', 'Ignored unsafe manifest workspace path.'])
  assert.deepEqual(resolveAssetLibraryOpenTarget(projected), {
    kind: 'unavailable',
    reason: 'Workspace asset is not openable.',
  })
  assert.deepEqual(resolveAssetLibraryOpenTarget(splat), {
    kind: 'unavailable',
    reason: 'Only safe images or .glb/.gltf workspace assets are openable in this release.',
  })
})

test('marks unsafe or non-openable entries unavailable without throwing', () => {
  const projected = projectAssetLibraryEntry({
    ...glbEntry,
    workspacePath: '../escape.glb',
    state: 'unsafe',
    openable: false,
    nonOpenableReason: 'Unsafe workspace path.',
  })
  assert.equal(projected.state, 'unsafe')
  assert.deepEqual(resolveAssetLibraryOpenTarget(projected), {
    kind: 'unavailable',
    reason: 'Unsafe workspace path.',
  })
})

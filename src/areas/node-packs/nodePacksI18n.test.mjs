import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

const nodePacksRoot = new URL('./', import.meta.url)
const repoRoot = new URL('../../../', import.meta.url)

async function read(relativeUrl) {
  return readFile(new URL(relativeUrl, nodePacksRoot), 'utf8')
}

test('Node Packs UI uses its canonical translation namespace', async () => {
  const [page, card, drawer, shared, adapter, i18n] = await Promise.all([
    read('./NodePacksPage.tsx'),
    read('./components/NodePackCard.tsx'),
    read('./components/NodePackDrawer.tsx'),
    read('./components/nodePackShared.tsx'),
    read('./i18n.ts'),
    readFile(new URL('src/shared/i18n.ts', repoRoot), 'utf8'),
  ])

  const uiSource = [page, card, drawer, shared].join('\n')
  const retiredNamespace = ['models', ''].join('.')
  assert.equal(uiSource.includes(`t('${retiredNamespace}`), false, 'Node Packs UI must use the canonical namespace')
  assert.match(uiSource, /t\('nodePacks\.installAll'/)
  assert.match(uiSource, /t\('nodePacks\.corruptedInstallation'/)
  assert.match(uiSource, /t\('nodePacks\.uninstallTitle'/)

  for (const literal of [
    '>Install all<',
    '>Install all nodes<',
    '>Official<',
    '>Local<',
    '>Repair<',
    '>Repairing…<',
    '>Description<',
    '>Details<',
    '>Dependencies<',
    '>Uninstall<',
    'aria-label="Clear search"',
    'title="Reload node packs"',
  ]) {
    assert.equal(uiSource.includes(literal), false, `Node Packs UI still hardcodes ${literal}`)
  }

  assert.match(adapter, /nodePacks\./)
  assert.doesNotMatch(adapter, /models\./)
  assert.match(page, /\.catch\(\(error: unknown\)/)
  assert.match(page, /setDownloadErrors\(/)
  assert.match(page, /downloadError=\{extDownloadError\(selectedExt\)\}/)
  assert.match(page, /getNodeDownloadId\(ext, node\)/)
  assert.match(page, /model\.download\(node\.hfRepo, downloadId/)
  assert.match(page, /!result\.success && !result\.paused && !result\.cancelled/)
  assert.match(page, /pauseDownload\(downloadId\)/)
  assert.match(page, /cancelDownload\(downloadId\)/)
  assert.match(drawer, /getNodeDownloadId\(ext, node\)/)
  assert.match(shared, /download\.location/)
  assert.match(drawer, /downloadError\?: string/)
  assert.match(drawer, /href=\{sourceUrl\}/)
  assert.match(drawer, /target="_blank"/)
  assert.match(drawer, /rel="noreferrer noopener"/)
  for (const key of [
    'nodePacks.installAll',
    'nodePacks.typeProcess',
    'nodePacks.corruptedInstallation',
    'nodePacks.repairHint',
    'nodePacks.uninstallTitle',
  ]) {
    const count = i18n.split(`'${key}'`).length - 1
    assert.equal(count, 2, `${key} must remain available in both dictionaries`)
  }
})

test('Node Packs consumes manifest locale metadata without changing machine ids', async () => {
  const [helper, store, manifestText] = await Promise.all([
    read('./nodePackI18n.ts'),
    readFile(new URL('src/shared/stores/nodePacksStore.ts', repoRoot), 'utf8'),
    readFile(new URL('node-packs/hunyuan3d-part/manifest.json', repoRoot), 'utf8'),
  ])
  const manifest = JSON.parse(manifestText)
  const node = manifest.nodes.find((item) => item.id === 'decompose-mesh')

  assert.equal(manifest.id, 'hunyuan3d-part')
  assert.equal(manifest.i18n['zh-CN'].name, 'Hunyuan3D-Part')
  assert.ok(manifest.i18n['zh-CN'].description)
  assert.equal(node.i18n['zh-CN'].name, '网格分件')
  assert.equal(node.id, 'decompose-mesh')
  assert.equal(node.input, 'mesh')
  assert.equal(node.output, 'mesh')

  assert.match(helper, /i18n\?\.\[language\]\?\.name/)
  assert.match(helper, /pack\.name/)
  assert.match(helper, /node\.name/)
  assert.match(store, /i18n: server\.i18n/)
  assert.match(store, /mergeNodeLocaleMetadata/)
})

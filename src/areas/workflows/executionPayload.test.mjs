import test from 'node:test'
import assert from 'node:assert/strict'
import { buildSync } from 'esbuild'
import { createRequire } from 'node:module'
import { mkdtempSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'

// executionPayload.ts only type-imports from runtime.d, so esbuild erases it.
function loadModule() {
  const outfile = join(mkdtempSync(join(tmpdir(), 'polykit-execpayload-test-')), 'executionPayload.cjs')
  const require = createRequire(import.meta.url)
  const result = buildSync({
    entryPoints: [resolve('src/areas/workflows/executionPayload.ts')],
    bundle: true,
    platform: 'node',
    format: 'cjs',
    write: false,
  })
  writeFileSync(outfile, result.outputFiles[0].text, 'utf8')
  return require(outfile)
}

const { compileServerWorkflow } = loadModule()

const PACKS = [
  {
    id: 'trellis2/generate', nodePackId: 'trellis2', nodePackName: 'Trellis.2 GGUF',
    nodePackAuthor: '', nodeId: 'generate', name: 'Generate Mesh', description: '',
    input: 'image', output: 'mesh', params: [], builtin: false, type: 'model',
  },
]
const node = (id, type, params = {}, nodePackId) =>
  ({ id, type, position: { x: 0, y: 0 }, data: { enabled: true, ...(nodePackId ? { nodePackId } : {}), params } })
const wf = (nodes, edges = []) => ({ id: 'wf', name: 'wf', description: '', nodes, edges, createdAt: '', updatedAt: '' })
const edge = (s, t, th) => ({ id: `${s}->${t}`, source: s, target: t, ...(th ? { targetHandle: th } : {}) })
const img = (id = 'img') => node(id, 'imageNode')
const model = (id = 'gen') => node(id, 'nodePackNode', {}, 'trellis2/generate')
const out = (id = 'out', enabled = true) => node(id, 'outputNode', {})

const PNG = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=='

test('valid image → model → output compiles with a payload', async () => {
  const res = await compileServerWorkflow(
    wf([img(), model(), out()], [edge('img', 'gen', 'input-0'), edge('gen', 'out')]),
    PACKS,
    { selectedImageData: PNG },
  )
  assert.equal(res.ok, true)
  assert.ok(res.ok && res.payload.prompt['gen'])
  assert.ok(res.ok && res.payload.output_node_id === 'out')
})

test('unsupported node type reports the exact node', async () => {
  const res = await compileServerWorkflow(wf([img(), node('bogus', 'bogusNode', {})]), PACKS)
  assert.equal(res.ok, false)
  assert.match(res.error, /bogusNode/)
})

test('mesh node with a server workspace path compiles to polykit.mesh', async () => {
  const meshSrc = node('mesh', 'meshNode', { source: 'file', filePath: 'Workflows/input.glb', fileName: 'input.glb' })
  const res = await compileServerWorkflow(
    wf([meshSrc, model(), out()], [edge('mesh', 'gen', 'input-0'), edge('gen', 'out')]),
    PACKS,
  )
  assert.equal(res.ok, true)
  assert.ok(res.ok && res.payload.prompt['mesh'])
  assert.deepEqual(res.ok && res.payload.prompt['mesh'].inputs.mesh, {
    kind: 'workspace_path',
    path: 'Workflows/input.glb',
  })
})

test('mesh node with a local-only path fails with a readable message', async () => {
  const meshSrc = node('mesh', 'meshNode', { source: 'file', filePath: 'web-file://temp/m.glb', fileName: 'm.glb' })
  const res = await compileServerWorkflow(wf([meshSrc, model(), out()], [edge('mesh', 'gen', 'input-0'), edge('gen', 'out')]), PACKS)
  assert.equal(res.ok, false)
  assert.match(res.error, /server can read/)
})

test('no executable node pack reports a clear message', async () => {
  const res = await compileServerWorkflow(wf([img()]), PACKS)
  assert.equal(res.ok, false)
  assert.match(res.error, /no model or process node/)
})

test('disabled output node is reported', async () => {
  const res = await compileServerWorkflow(
    wf([img(), model(), { ...out('out'), data: { enabled: false } }], [edge('img', 'gen', 'input-0'), edge('gen', 'out')]),
    PACKS,
    { selectedImageData: PNG },
  )
  assert.equal(res.ok, false)
  assert.match(res.error, /output node is disabled/)
})

test('output node with no incoming mesh is reported', async () => {
  const res = await compileServerWorkflow(
    wf([img(), model(), out()], [edge('img', 'gen', 'input-0')]),
    PACKS,
    { selectedImageData: PNG },
  )
  assert.equal(res.ok, false)
  assert.match(res.error, /isn't connected to a mesh/)
})

test('image node without readable data is reported with a stale-file hint', async () => {
  // No selectedImageData and no file bridge → readBase64 resolves undefined.
  const res = await compileServerWorkflow(
    wf([img(), model(), out()], [edge('img', 'gen', 'input-0'), edge('gen', 'out')]),
    PACKS,
  )
  assert.equal(res.ok, false)
  assert.match(res.error, /no readable image/)
})

test('image node with a workspace path compiles to a workspace_path reference', async () => {
  // filePath is workspace-relative (uploaded to the server) — no base64 needed.
  const imgNode = { id: 'img', type: 'imageNode', position: { x: 0, y: 0 }, data: { enabled: true, params: { filePath: 'Workflows/hero.png' } } }
  const res = await compileServerWorkflow(
    wf([imgNode, model(), out()], [edge('img', 'gen', 'input-0'), edge('gen', 'out')]),
    PACKS,
  )
  assert.equal(res.ok, true)
  assert.ok(res.ok && res.payload.prompt['img'])
  assert.deepEqual(res.ok && res.payload.prompt['img'].inputs.image, { kind: 'workspace_path', path: 'Workflows/hero.png' })
})

test('image node with a browser temp path falls back to persisted preview base64', async () => {
  // web-file:// temp path is gone (reload) but the persisted preview data-URL exists.
  const imgNode = {
    id: 'img', type: 'imageNode', position: { x: 0, y: 0 },
    data: { enabled: true, params: { filePath: 'web-file://dead/beef.png', preview: `data:image/png;base64,${PNG}` } },
  }
  const res = await compileServerWorkflow(
    wf([imgNode, model(), out()], [edge('img', 'gen', 'input-0'), edge('gen', 'out')]),
    PACKS,
  )
  assert.equal(res.ok, true)
  assert.ok(res.ok && res.payload.prompt['img'].inputs.image)
  assert.equal(res.ok && res.payload.prompt['img'].inputs.image.kind, 'base64')
  assert.equal(res.ok && res.payload.prompt['img'].inputs.image.data, PNG)
})

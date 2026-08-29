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
  {
    id: 'trellis2/refine', nodePackId: 'trellis2', nodePackName: 'Trellis.2 GGUF',
    nodePackAuthor: '', nodeId: 'refine', name: 'Texture Mesh', description: '',
    input: 'image', inputs: ['image', 'mesh'], output: 'mesh', params: [
      { id: 'texture_resolution', label: 'Texture Resolution', type: 'select', default: 1024 },
      { id: 'texture_size', label: 'Texture Atlas Size', type: 'select', default: 2048 },
      { id: 'texture_steps', label: 'Texture Steps', type: 'int', default: 12 },
      { id: 'texture_guidance', label: 'Guidance Strength', type: 'float', default: 3.0 },
      { id: 'foreground_ratio', label: 'Foreground Ratio', type: 'float', default: 0.85 },
    ], builtin: false, type: 'model',
  },
  {
    id: 'anima/generate', nodePackId: 'anima', nodePackName: 'Anima', nodePackAuthor: 'CircleStone Labs', nodeId: 'generate', name: 'Generate Illustration', description: '',
    input: 'text', output: 'image', params: [], builtin: true, type: 'model',
  },
  {
    id: 'image-background-remover/remove-background', nodePackId: 'image-background-remover', nodePackName: 'Image Background Remover', nodePackAuthor: 'PolyKit', nodeId: 'remove-background', name: 'Remove Background', description: '',
    input: 'image', output: 'image', params: [{ id: 'model', label: 'Segmentation Model', type: 'select', default: 'isnet-anime' }], builtin: true, type: 'process',
  },
]
const node = (id, type, params = {}, nodePackId) =>
  ({ id, type, position: { x: 0, y: 0 }, data: { enabled: true, ...(nodePackId ? { nodePackId } : {}), params } })
const wf = (nodes, edges = []) => ({ id: 'wf', name: 'wf', description: '', nodes, edges, createdAt: '', updatedAt: '' })
const edge = (s, t, th) => ({ id: `${s}->${t}`, source: s, target: t, ...(th ? { targetHandle: th } : {}) })
const img = (id = 'img') => node(id, 'imageNode')
const model = (id = 'gen') => node(id, 'nodePackNode', {}, 'trellis2/generate')
const refineModel = (id = 'refine') => node(id, 'nodePackNode', {}, 'trellis2/refine')
const animaModel = (id = 'anima') => node(id, 'nodePackNode', {}, 'anima/generate')
const cutoutModel = (id = 'cutout') => node(id, 'nodePackNode', {}, 'image-background-remover/remove-background')
const out = (id = 'out', enabled = true) => node(id, 'outputNode', {})
const preview = (id = 'preview') => node(id, 'previewNode', {})

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

test('selected output compiles a partial execution target', async () => {
  const res = await compileServerWorkflow(
    wf([img(), model(), out()], [edge('img', 'gen', 'input-0'), edge('gen', 'out')]),
    PACKS,
    { selectedImageData: PNG, targetNodeId: 'out' },
  )
  assert.equal(res.ok, true)
  assert.deepEqual(res.ok && res.payload.target_node_ids, ['out'])
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

test('an explicit image-node workspace binding wins over its base64 preview', async () => {
  const imgNode = {
    id: 'img', type: 'imageNode', position: { x: 0, y: 0 },
    data: { enabled: true, params: { filePath: 'web-file://temporary/hero.png', preview: `data:image/png;base64,${PNG}` } },
  }
  const res = await compileServerWorkflow(
    wf([imgNode, model(), out()], [edge('img', 'gen', 'input-0'), edge('gen', 'out')]),
    PACKS,
    { imageNodeWorkspacePaths: { img: 'Workflows/hero.png' } },
  )
  assert.equal(res.ok, true)
  assert.deepEqual(res.ok && res.payload.prompt['img'].inputs.image, {
    kind: 'workspace_path', path: 'Workflows/hero.png',
  })
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

test('text → Anima → image preview compiles with an image sink input', async () => {
  const text = node('text', 'textNode', { text: 'single low-poly observatory, cel shading' })
  const anima = node('anima', 'nodePackNode', {}, 'anima/generate')
  const res = await compileServerWorkflow(
    wf([text, anima, preview()], [edge('text', 'anima', 'input-0'), edge('anima', 'preview')]),
    PACKS,
  )
  assert.equal(res.ok, true)
  assert.ok(res.ok && res.payload.prompt.preview)
  assert.deepEqual(res.ok && res.payload.prompt.preview.inputs.image, ['anima', 'image'])
})

test('text → Anima → cutout → Trellis.2 → texture → mesh output compiles as one pipeline', async () => {
  const text = node('text', 'textNode', { text: 'single stylized anime character, full body, plain background' })
  const anima = animaModel()
  const cutout = cutoutModel()
  const trellis = model('trellis')
  const refine = refineModel()
  const res = await compileServerWorkflow(
    wf(
      [text, anima, cutout, trellis, refine, out()],
      [
        edge('text', 'anima', 'input-0'),
        edge('anima', 'cutout', 'input-0'),
        edge('cutout', 'trellis', 'input-0'),
        edge('cutout', 'refine', 'input-0'),
        edge('trellis', 'refine', 'input-1'),
        edge('refine', 'out'),
      ],
    ),
    PACKS,
  )

  assert.equal(res.ok, true)
  assert.ok(res.ok && res.payload.output_node_id === 'out')
  assert.deepEqual(res.ok && res.payload.prompt.anima.inputs.text, ['text', 'text'])
  assert.deepEqual(res.ok && res.payload.prompt.cutout.inputs.image, ['anima', 'image'])
  assert.deepEqual(res.ok && res.payload.prompt.trellis.inputs.image, ['cutout', 'image'])
  assert.deepEqual(res.ok && res.payload.prompt.refine.inputs.image, ['cutout', 'image'])
  assert.deepEqual(res.ok && res.payload.prompt.refine.inputs.mesh, ['trellis', 'mesh'])
  assert.equal(res.ok && res.payload.prompt.refine.inputs.params.texture_size, 2048)
  assert.deepEqual(res.ok && res.payload.prompt.out.inputs.mesh, ['refine', 'mesh'])
})

test('running to an intermediate node adds a transient typed output sink', async () => {
  const text = node('text', 'textNode', { text: 'single stylized anime character, full body, plain background' })
  const anima = animaModel()
  const res = await compileServerWorkflow(
    wf(
      [text, anima],
      [edge('text', 'anima', 'input-0')],
    ),
    PACKS,
    { targetNodeId: 'anima' },
  )

  assert.equal(res.ok, true)
  assert.ok(res.ok && res.payload.output_node_id === '__run_target__anima')
  assert.deepEqual(res.ok && res.payload.target_node_ids, ['__run_target__anima'])
  assert.equal(res.ok && res.payload.prompt.__run_target__anima.class_type, 'polykit.image_output')
  assert.deepEqual(res.ok && res.payload.prompt.__run_target__anima.inputs.image, ['anima', 'image'])
})

test('intermediate runs ignore an unrelated disconnected final output', async () => {
  const text = node('text', 'textNode', { text: 'single stylized anime character' })
  const anima = animaModel()
  const disconnectedOutput = out('out')
  const res = await compileServerWorkflow(
    wf(
      [text, anima, disconnectedOutput],
      [edge('text', 'anima', 'input-0')],
    ),
    PACKS,
    { targetNodeId: 'anima' },
  )

  assert.equal(res.ok, true)
  assert.ok(res.ok && res.payload.prompt.__run_target__anima)
  assert.equal(res.ok && res.payload.prompt.out, undefined)
})

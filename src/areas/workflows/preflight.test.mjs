import test from 'node:test'
import assert from 'node:assert/strict'
import { buildSync } from 'esbuild'
import { createRequire } from 'node:module'
import { mkdtempSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'

// Bundle preflight.ts (and its real dependency mockNodePacks.ts) into CJS.
// All @shared/* imports in those files are type-only, so esbuild erases them
// and no path-alias resolution is required.
function loadModule() {
  const dir = mkdtempSync(join(tmpdir(), 'polykit-preflight-test-'))
  const outfile = join(dir, 'preflight.cjs')
  const storeStub = join(dir, 'app-store-stub.mjs')
  writeFileSync(storeStub, 'export const useAppStore = { getState: () => ({ language: \'en-US\' }) }\n', 'utf8')
  const require = createRequire(import.meta.url)
  const result = buildSync({
    entryPoints: [resolve('src/areas/workflows/preflight.ts')],
    bundle: true,
    platform: 'node',
    format: 'cjs',
    write: false,
    alias: { '@shared/stores/appStore': storeStub },
  })
  writeFileSync(outfile, result.outputFiles[0].text, 'utf8')
  return require(outfile)
}

// ─── Fixtures ────────────────────────────────────────────────────────────────

function ext(overrides = {}) {
  return {
    id: 'pack/process-node',
    nodePackId: 'pack',
    nodePackName: 'Pack',
    nodePackAuthor: 'tester',
    nodeId: 'process-node',
    name: 'Process Node',
    description: '',
    input: 'image',
    output: 'mesh',
    params: [],
    builtin: false,
    type: 'process',
    ...overrides,
  }
}

function wf(nodes, edges = []) {
  return { id: 'wf', name: 'wf', description: '', nodes, edges, createdAt: '', updatedAt: '' }
}

const imageNode = (id = 'img') => ({ id, type: 'imageNode', position: { x: 0, y: 0 }, data: { params: { filePath: '/tmp/a.png' } } })
const textNode = (id = 'txt') => ({ id, type: 'textNode', position: { x: 0, y: 0 }, data: {} })

// ─── Tests ───────────────────────────────────────────────────────────────────

test('valid graph (image → extension expecting image) produces no issues', () => {
  const { validateWorkflowPreflight } = loadModule()
  const extensions = [ext()]
  const workflow = wf(
    [imageNode('img'), { id: 'proc', type: 'nodePackNode', position: { x: 0, y: 0 }, data: { nodePackId: 'pack/process-node' } }],
    [{ id: 'e1', source: 'img', target: 'proc' }],
  )

  assert.deepEqual(validateWorkflowPreflight(workflow, extensions), [])
})

test('extension node with no matching incoming connection is flagged', () => {
  const { validateWorkflowPreflight } = loadModule()
  const extensions = [ext()]
  const workflow = wf(
    [{ id: 'proc', type: 'nodePackNode', position: { x: 0, y: 0 }, data: { nodePackId: 'pack/process-node' } }],
    [],
  )

  const issues = validateWorkflowPreflight(workflow, extensions)
  assert.equal(issues.length, 1)
  assert.equal(issues[0].key, 'proc:missing:image')
  assert.match(issues[0].message, /needs an incoming image connection/)
})

test('type mismatch on an incoming edge is flagged', () => {
  const { validateWorkflowPreflight } = loadModule()
  const extensions = [ext()] // expects image
  const workflow = wf(
    [textNode('txt'), { id: 'proc', type: 'nodePackNode', position: { x: 0, y: 0 }, data: { nodePackId: 'pack/process-node' } }],
    [{ id: 'e1', source: 'txt', target: 'proc' }],
  )

  const issues = validateWorkflowPreflight(workflow, extensions)
  // missing required image input AND a text→image type mismatch
  assert.ok(issues.some((i) => i.key === 'proc:missing:image'))
  assert.ok(issues.some((i) => i.key === 'proc:type:e1' && /outputs text/.test(i.message)))
})

test('unknown extension id is reported as unavailable', () => {
  const { validateWorkflowPreflight } = loadModule()
  const workflow = wf(
    [{ id: 'proc', type: 'nodePackNode', position: { x: 0, y: 0 }, data: { nodePackId: 'ghost/node' } }],
    [],
  )

  const issues = validateWorkflowPreflight(workflow, [])
  assert.equal(issues.length, 1)
  assert.equal(issues[0].key, 'proc:missing-node-pack')
  assert.match(issues[0].message, /unavailable/)
})

test('meshNode without an explicit file flags missing mesh input', () => {
  const { validateWorkflowPreflight } = loadModule()
  const workflow = wf([
    { id: 'mesh', type: 'meshNode', position: { x: 0, y: 0 }, data: { params: {} } },
  ])

  const issues = validateWorkflowPreflight(workflow, [])
  assert.equal(issues.length, 1)
  assert.equal(issues[0].key, 'mesh:no-mesh')
  assert.match(issues[0].message, /Upload local mesh/)
})

test('multi-input extension requires every declared input type', () => {
  const { validateWorkflowPreflight } = loadModule()
  const extensions = [ext({ inputs: ['image', 'text'] })]
  const workflow = wf(
    [imageNode('img'), { id: 'proc', type: 'nodePackNode', position: { x: 0, y: 0 }, data: { nodePackId: 'pack/process-node' } }],
    [{ id: 'e1', source: 'img', target: 'proc' }],
  )

  const issues = validateWorkflowPreflight(workflow, extensions)
  // image satisfied, text still missing
  assert.ok(!issues.some((i) => i.key === 'proc:missing:image'))
  assert.ok(issues.some((i) => i.key === 'proc:missing:text'))
})

test('web preflight allows process node packs (server executes them)', () => {
  const { validateWorkflowPreflight } = loadModule()
  const workflow = wf([
    imageNode('img'),
    { id: 'proc', type: 'nodePackNode', position: { x: 0, y: 0 }, data: { nodePackId: 'pack/process-node' } },
  ])

  const issues = validateWorkflowPreflight(workflow, [ext()], { executionMode: 'web' })
  assert.equal(issues.some((issue) => issue.key === 'web-execution-unsupported'), false)
})

test('web preflight rejects control-flow nodes with a precise message', () => {
  const { validateWorkflowPreflight } = loadModule()
  const workflow = wf([
    imageNode('img'),
    { id: 'loop', type: 'forEachNode', position: { x: 0, y: 0 }, data: { params: { mode: 'image', dir: '/tmp' } } },
  ])

  const issues = validateWorkflowPreflight(workflow, [ext()], { executionMode: 'web' })
  const issue = issues.find((i) => i.key === 'web-execution-unsupported')
  assert.ok(issue)
  assert.match(issue.message, /can't execute/)
  assert.ok(!/desktop/i.test(issue.message), 'must not tell users to switch shells')
})

test('image node without an image reports a precise issue', () => {
  const { validateWorkflowPreflight } = loadModule()
  const workflow = wf([
    { id: 'img', type: 'imageNode', position: { x: 0, y: 0 }, data: { params: {} } },
  ])

  const issues = validateWorkflowPreflight(workflow, [], { executionMode: 'web' })
  const issue = issues.find((i) => i.key === 'img:no-image')
  assert.ok(issue)
  assert.match(issue.message, /Browse/)
})

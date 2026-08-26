import test from 'node:test'
import assert from 'node:assert/strict'
import { buildSync } from 'esbuild'
import { createRequire } from 'node:module'
import { mkdtempSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'

// nodeBehaviors.ts only type-imports from runtime.d, so esbuild erases it.
function loadModule() {
  const outfile = join(mkdtempSync(join(tmpdir(), 'polykit-nodebehaviors-test-')), 'nodeBehaviors.cjs')
  const require = createRequire(import.meta.url)
  const result = buildSync({
    entryPoints: [resolve('src/areas/workflows/nodeBehaviors.ts')],
    bundle: true,
    platform: 'node',
    format: 'cjs',
    write: false,
  })
  writeFileSync(outfile, result.outputFiles[0].text, 'utf8')
  return require(outfile)
}

const {
  isSceneOutput, resolveDataSource, reachesSceneOutput,
} = loadModule()

// ─── Fixtures ────────────────────────────────────────────────────────────────

const node = (id, type) => ({ id, type, position: { x: 0, y: 0 }, data: {} })
const edge = (source, target) => ({ id: `${source}->${target}`, source, target })
const mapOf = (...nodes) => new Map(nodes.map((n) => [n.id, n]))

// ─── Behavior predicates ───────────────────────────────────────────────────────

test('isSceneOutput reads the behavior table and tolerates unknown/undefined types', () => {
  assert.equal(isSceneOutput('outputNode'), true)
  assert.equal(isSceneOutput('imageNode'), false)
  assert.equal(isSceneOutput('nodePackNode'), false)
  assert.equal(isSceneOutput(undefined), false)
  assert.equal(isSceneOutput('ghostNode'), false)
})

// ─── resolveDataSource ─────────────────────────────────────────────────────────

test('resolveDataSource returns the source id directly (no passthrough nodes)', () => {
  assert.equal(resolveDataSource('img'), 'img')
  assert.equal(resolveDataSource('proc'), 'proc')
})

// ─── reachesSceneOutput ────────────────────────────────────────────────────────

test('reachesSceneOutput follows forward paths to a scene output', () => {
  const nodes = mapOf(node('proc', 'nodePackNode'), node('out', 'outputNode'))
  const edges = [edge('proc', 'out')]
  assert.equal(reachesSceneOutput('proc', edges, nodes), true)
})

test('reachesSceneOutput is false when no path reaches an output', () => {
  const nodes = mapOf(node('proc', 'nodePackNode'), node('img', 'imageNode'))
  const edges = [edge('proc', 'img')]
  assert.equal(reachesSceneOutput('proc', edges, nodes), false)
})

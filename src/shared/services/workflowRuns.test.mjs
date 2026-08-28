import test from 'node:test'
import assert from 'node:assert/strict'
import { buildSync } from 'esbuild'
import { createRequire } from 'node:module'
import { mkdtempSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'

function loadModule() {
  const dir = mkdtempSync(join(tmpdir(), 'polykit-workflow-runs-test-'))
  const axiosStub = join(dir, 'axios-stub.mjs')
  writeFileSync(axiosStub, `
    function response(base, method, url, body, config) {
      const full = (base ?? '') + url
      globalThis.__calls.push({ method, url: full, body, config })
      const value = globalThis.__responses[full]
      const data = Array.isArray(value) ? value.shift() : value
      return Promise.resolve({ data: data ?? {} })
    }
    export default {
      create: (cfg) => ({
        get: (url, config) => response(cfg?.baseURL, 'get', url, undefined, config),
        post: (url, body, config) => response(cfg?.baseURL, 'post', url, body, config),
      }),
    }
  `, 'utf8')

  const outfile = join(dir, 'workflowRuns.cjs')
  const require = createRequire(import.meta.url)
  const result = buildSync({
    entryPoints: [resolve('src/shared/services/workflowRuns.ts')],
    bundle: true,
    platform: 'node',
    format: 'cjs',
    write: false,
    alias: { axios: axiosStub },
  })
  writeFileSync(outfile, result.outputFiles[0].text, 'utf8')
  return require(outfile)
}

function reset() {
  globalThis.__calls = []
  globalThis.__responses = {}
}

test('submit posts a compiled prompt to the canonical execute endpoint', async () => {
  reset()
  const { createWorkflowRunsClient } = loadModule()
  globalThis.__responses['http://test.local/workflow-runs/execute'] = { run_id: 'run-1', status: 'pending' }

  const result = await createWorkflowRunsClient('http://test.local').submit({ workflow_id: 'wf-1' })

  assert.deepEqual(result, { run_id: 'run-1', status: 'pending' })
  assert.equal(globalThis.__calls[0].url, 'http://test.local/workflow-runs/execute')
  assert.deepEqual(globalThis.__calls[0].body, { workflow_id: 'wf-1' })
})

test('poll reports progress and resolves at a terminal server status', async () => {
  reset()
  const { createWorkflowRunsClient } = loadModule()
  const endpoint = 'http://test.local/workflow-runs/run-1'
  globalThis.__responses[endpoint] = [
    { run_id: 'run-1', status: 'running', progress: 25, step: 'Generate' },
    { run_id: 'run-1', status: 'done', progress: 100, output_url: '/workspace/out.glb' },
  ]
  const updates = []

  const result = await createWorkflowRunsClient('http://test.local').poll('run-1', {
    intervalMs: 0,
    onUpdate: (status) => updates.push(status.status),
  })

  assert.equal(result.status, 'done')
  assert.deepEqual(updates, ['running', 'done'])
  assert.deepEqual(globalThis.__calls.map((call) => call.url), [endpoint, endpoint])
})

test('cancel posts to the run-specific canonical endpoint', async () => {
  reset()
  const { createWorkflowRunsClient } = loadModule()
  await createWorkflowRunsClient('http://test.local').cancel('run/1')
  assert.equal(globalThis.__calls[0].url, 'http://test.local/workflow-runs/run%2F1/cancel')
})

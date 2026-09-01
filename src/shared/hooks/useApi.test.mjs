import test from 'node:test'
import assert from 'node:assert/strict'
import { buildSync } from 'esbuild'
import { createRequire } from 'node:module'
import { mkdtempSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'

// Bundle useApi.ts with stubbed dependencies:
//   - axios            → records requests and returns canned responses
//   - appStore         → useAppStore returns a fixed apiUrl (no React runtime)
// The stub modules communicate with the test through globalThis.
function loadUseApi() {
  const dir = mkdtempSync(join(tmpdir(), 'polykit-useapi-test-'))

  const axiosStub = join(dir, 'axios-stub.mjs')
  writeFileSync(axiosStub, `
    function record(base, method, url, body) {
      const full = (base ?? '') + url
      globalThis.__calls.push({ method, url: full, body })
      const r = globalThis.__responses[full]
      return Promise.resolve({ data: r ?? {} })
    }
    export default {
      create: (cfg) => ({
        get:    (url) => record(cfg?.baseURL, 'get', url),
        post:   (url, body) => record(cfg?.baseURL, 'post', url, body),
        delete: (url) => record(cfg?.baseURL, 'delete', url),
      }),
    }
  `, 'utf8')

  const storeStub = join(dir, 'store-stub.mjs')
  writeFileSync(storeStub, `
    export const useAppStore = (sel) => sel({ apiUrl: 'http://test.local' })
    export const GenerationOptions = {}
  `, 'utf8')

  const outfile = join(dir, 'useApi.cjs')
  const require = createRequire(import.meta.url)
  const result = buildSync({
    entryPoints: [resolve('src/shared/hooks/useApi.ts')],
    bundle: true,
    platform: 'node',
    format: 'cjs',
    write: false,
    alias: {
      axios: axiosStub,
      '@shared/stores/appStore': storeStub,
    },
  })
  writeFileSync(outfile, result.outputFiles[0].text, 'utf8')
  return require(outfile).useApi
}

function reset() {
  globalThis.__calls = []
  globalThis.__responses = {}
}

test('exposes the browser-safe methods consumed by the app', () => {
  reset()
  const api = loadUseApi()()
  for (const name of [
    'generateFromImage', 'pollJobStatus', 'cancelJob', 'optimizeMesh', 'smoothMesh',
    'importMesh', 'transformMesh', 'exportMesh',
  ]) {
    assert.equal(typeof api[name], 'function', `useApi() must expose ${name}`)
  }
  for (const removed of ['getModelStatus', 'getAllModelsStatus', 'downloadModel']) {
    assert.equal(api[removed], undefined, `${removed} must not return to the shared Web API hook`)
  }
})

test('pollJobStatus uses canonical Run status and maps output_url → outputUrl', async () => {
  reset()
  globalThis.__responses['http://test.local/runs/job1'] = {
    status: 'done', progress: 100, output_url: '/workspace/out.glb',
  }
  const api = loadUseApi()()

  const result = await api.pollJobStatus('job1')

  assert.equal(globalThis.__calls[0].url, 'http://test.local/runs/job1')
  assert.equal(result.status, 'done')
  assert.equal(result.outputUrl, '/workspace/out.glb')
})

test('optimizeMesh maps face_count → faceCount and posts target_faces', async () => {
  reset()
  globalThis.__responses['http://test.local/optimize/mesh'] = { url: '/o.glb', face_count: 5000 }
  const api = loadUseApi()()

  const result = await api.optimizeMesh('/in.glb', 5000)

  const call = globalThis.__calls[0]
  assert.equal(call.url, 'http://test.local/optimize/mesh')
  assert.deepEqual(call.body, { path: '/in.glb', target_faces: 5000 })
  assert.deepEqual(result, { url: '/o.glb', faceCount: 5000 })
})

test('cancelJob deletes the canonical Run endpoint', async () => {
  reset()
  const api = loadUseApi()()
  await api.cancelJob('job9')
  assert.equal(globalThis.__calls[0].method, 'delete')
  assert.equal(globalThis.__calls[0].url, 'http://test.local/runs/job9')
})

test('generateFromImage posts multipart to workflow-runs and maps run_id → jobId', async () => {
  reset()
  globalThis.__responses['http://test.local/workflow-runs/from-image'] = { run_id: 'run42' }
  const api = loadUseApi()()

  const options = {
    modelId: 'model-x', remesh: 'none', enableTexture: true,
    textureResolution: 1024, modelParams: { guidance: 3.5 },
  }
  const result = await api.generateFromImage('/img.png', options, btoa('fake-png-bytes'))

  const call = globalThis.__calls[0]
  assert.equal(call.url, 'http://test.local/workflow-runs/from-image')
  assert.ok(call.body instanceof FormData)
  assert.equal(call.body.get('model_id'), 'model-x')
  assert.equal(call.body.get('remesh'), 'none')
  assert.equal(call.body.get('enable_texture'), 'true')
  assert.equal(call.body.get('texture_resolution'), '1024')
  assert.deepEqual(JSON.parse(call.body.get('params')), { guidance: 3.5 })
  assert.equal(result.jobId, 'run42')
})

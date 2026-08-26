import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

const manifestUrl = new URL('../../../node-packs/trellis2/manifest.json', import.meta.url)

async function loadManifest() {
  return JSON.parse(await readFile(manifestUrl, 'utf8'))
}

function paramsById(node) {
  return new Map((node.params_schema ?? []).map((param) => [param.id, param]))
}

test('Trellis parameter i18n is presentation-only', async () => {
  const manifest = await loadManifest()
  const generate = manifest.nodes.find((node) => node.id === 'generate')
  const refine = manifest.nodes.find((node) => node.id === 'refine')
  assert.ok(generate)
  assert.ok(refine)

  const generateParams = paramsById(generate)
  assert.deepEqual([...generateParams.keys()], [
    'pipeline_type',
    'gguf_quant',
    'ss_steps',
    'slat_steps',
    'foreground_ratio',
    'remesh_resolution',
    'seed',
  ])

  // Machine values are stable and remain suitable for configs/docs/API calls.
  assert.equal(generateParams.get('pipeline_type').default, '1024_cascade')
  assert.deepEqual(
    generateParams.get('pipeline_type').options.map((option) => option.value),
    ['512', '1024', '1024_cascade', '1536_cascade'],
  )
  assert.deepEqual(
    generateParams.get('gguf_quant').options.map((option) => option.value),
    ['Q4_K_M', 'Q5_K_M', 'Q6_K', 'Q8_0'],
  )
  assert.equal(generateParams.get('seed').i18n['zh-CN'].label, 'Seed')

  // Every Trellis parameter supplies Chinese presentation text while keeping
  // technical vocabulary such as GGUF, SLaT, VRAM, Guidance and Seed available.
  for (const node of [generate, refine]) {
    for (const param of node.params_schema ?? []) {
      const locale = param.i18n?.['zh-CN']
      assert.ok(locale?.label, `${node.id}.${param.id} is missing zh-CN label`)
      assert.ok(locale?.tooltip, `${node.id}.${param.id} is missing zh-CN tooltip`)
    }
  }

  assert.equal(generate.i18n['zh-CN'].name, '生成网格')
  assert.equal(refine.i18n['zh-CN'].name, '纹理网格')
})

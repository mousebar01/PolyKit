import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

const templateUrl = new URL('./hunyuan3d-part-segmentation.json', import.meta.url)

async function loadTemplate() {
  return JSON.parse(await readFile(templateUrl, 'utf8'))
}

test('mesh segmentation template is a mesh -> provider -> output workflow', async () => {
  const template = await loadTemplate()

  assert.equal(template.templateId, 'hunyuan3d-part-segmentation')
  assert.deepEqual(template.requires, ['hunyuan3d-part'])

  const meshInput = template.nodes.find((node) => node.id === 'mesh-input')
  const segment = template.nodes.find((node) => node.id === 'segment-parts')
  const output = template.nodes.find((node) => node.id === 'parts-output')

  assert.equal(meshInput?.type, 'meshNode')
  assert.equal(segment?.type, 'nodePackNode')
  assert.equal(segment?.data?.nodePackId, 'hunyuan3d-part/decompose-mesh')
  assert.equal(output?.type, 'outputNode')
  assert.equal(segment?.data?.params?.pipeline_stage, 'p3-sam')
  assert.equal(segment?.data?.params?.output_mode, 'primary')
  assert.equal(segment?.data?.params?.semantic_resolver, 'off')
  assert.equal(segment?.data?.params?.export_format, 'glb')

  assert.deepEqual(
    template.edges.map((edge) => [edge.source, edge.target]),
    [
      ['mesh-input', 'segment-parts'],
      ['segment-parts', 'parts-output'],
    ],
  )
})

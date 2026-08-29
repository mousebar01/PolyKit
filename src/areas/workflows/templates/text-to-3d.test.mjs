import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

const templateUrl = new URL('./anima-trellis2-text-to-3d.json', import.meta.url)

async function loadTemplate() {
  return JSON.parse(await readFile(templateUrl, 'utf8'))
}

test('text to 3D template composes Anima, cutout, Trellis.2 texture, and scene output', async () => {
  const template = await loadTemplate()

  assert.equal(template.templateId, 'anima-trellis2-text-to-3d')
  assert.equal(template.nodePackId, 'anima')
  assert.deepEqual(template.requires, ['anima', 'image-background-remover', 'trellis2'])

  const text = template.nodes.find((node) => node.id === 'text-input')
  const anima = template.nodes.find((node) => node.id === 'generate-image')
  const cutout = template.nodes.find((node) => node.id === 'remove-background')
  const trellis = template.nodes.find((node) => node.id === 'generate-mesh')
  const texture = template.nodes.find((node) => node.id === 'texture-mesh')
  const output = template.nodes.find((node) => node.id === 'scene-output')

  assert.equal(text?.type, 'textNode')
  assert.equal(text?.data?.params?.text, '')
  assert.equal(anima?.type, 'nodePackNode')
  assert.equal(anima?.data?.nodePackId, 'anima/generate')
  assert.equal(cutout?.type, 'nodePackNode')
  assert.equal(cutout?.data?.nodePackId, 'image-background-remover/remove-background')
  assert.equal(cutout?.data?.params?.model, 'isnet-anime')
  assert.equal(trellis?.type, 'nodePackNode')
  assert.equal(trellis?.data?.nodePackId, 'trellis2/generate')
  assert.equal(texture?.type, 'nodePackNode')
  assert.equal(texture?.data?.nodePackId, 'trellis2/refine')
  assert.equal(texture?.data?.params?.texture_resolution, 1024)
  assert.equal(texture?.data?.params?.texture_size, 2048)
  assert.equal(output?.type, 'outputNode')

  assert.deepEqual(
    template.edges.map((edge) => [edge.source, edge.target, edge.targetHandle ?? null]),
    [
      ['text-input', 'generate-image', 'input-0'],
      ['generate-image', 'remove-background', 'input-0'],
      ['remove-background', 'generate-mesh', 'input-0'],
      ['generate-mesh', 'texture-mesh', 'input-1'],
      ['remove-background', 'texture-mesh', 'input-0'],
      ['texture-mesh', 'scene-output', null],
    ],
  )
})

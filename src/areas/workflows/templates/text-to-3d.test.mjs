import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

const templateUrl = new URL('./anima-trellis2-text-to-3d.json', import.meta.url)

async function loadTemplate() {
  return JSON.parse(await readFile(templateUrl, 'utf8'))
}

test('text to 3D template composes Anima, Trellis.2, and scene output', async () => {
  const template = await loadTemplate()

  assert.equal(template.templateId, 'anima-trellis2-text-to-3d')
  assert.equal(template.nodePackId, 'anima')
  assert.deepEqual(template.requires, ['anima', 'trellis2'])

  const text = template.nodes.find((node) => node.id === 'text-input')
  const anima = template.nodes.find((node) => node.id === 'generate-image')
  const trellis = template.nodes.find((node) => node.id === 'generate-mesh')
  const output = template.nodes.find((node) => node.id === 'scene-output')

  assert.equal(text?.type, 'textNode')
  assert.equal(text?.data?.params?.text, '')
  assert.equal(anima?.type, 'nodePackNode')
  assert.equal(anima?.data?.nodePackId, 'anima/generate')
  assert.equal(trellis?.type, 'nodePackNode')
  assert.equal(trellis?.data?.nodePackId, 'trellis2/generate')
  assert.equal(output?.type, 'outputNode')

  assert.deepEqual(
    template.edges.map((edge) => [edge.source, edge.target, edge.targetHandle ?? null]),
    [
      ['text-input', 'generate-image', 'input-0'],
      ['generate-image', 'generate-mesh', 'input-0'],
      ['generate-mesh', 'scene-output', null],
    ],
  )
})

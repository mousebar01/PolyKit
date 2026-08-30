import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

const templateUrl = new URL('./scene-composition.json', import.meta.url)

test('scene composition template fans multiple meshes into one GLB output', async () => {
  const template = JSON.parse(await readFile(templateUrl, 'utf8'))
  assert.equal(template.templateId, 'scene-composition')
  assert.deepEqual(template.requires, ['scene-composer'])
  assert.equal(template.nodes.find((node) => node.id === 'compose')?.data?.nodePackId, 'scene-composer/compose')
  assert.deepEqual(
    template.edges.filter((edge) => edge.target === 'compose').map((edge) => [edge.source, edge.targetHandle]),
    [['mesh-a', 'input-0'], ['mesh-b', 'input-0']],
  )
})

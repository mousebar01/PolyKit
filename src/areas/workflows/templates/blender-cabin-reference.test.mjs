import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

const templateUrl = new URL('./blender-cabin-reference.json', import.meta.url)

test('Blender cabin reference template wires a semantic brief to the MCP scene builder', async () => {
  const template = JSON.parse(await readFile(templateUrl, 'utf8'))
  assert.equal(template.templateId, 'blender-cabin-reference')
  assert.deepEqual(template.requires, ['blender-scene'])
  assert.equal(template.nodes.find((node) => node.id === 'build-scene')?.data?.nodePackId, 'blender-scene/build')
  assert.equal(template.nodes.find((node) => node.id === 'build-scene')?.data?.params?.preset, 'cabin')
  assert.deepEqual(
    template.edges.map((edge) => [edge.source, edge.target, edge.targetHandle ?? null]),
    [
      ['scene-brief', 'build-scene', 'input-0'],
      ['build-scene', 'scene-output', null],
    ],
  )
})

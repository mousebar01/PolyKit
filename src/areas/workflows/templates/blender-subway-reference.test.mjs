import assert from 'node:assert/strict'
import { readFile, stat } from 'node:fs/promises'
import test from 'node:test'

const templateUrl = new URL('./blender-subway-reference.json', import.meta.url)
const exampleDir = new URL('../../../../examples/blender-subway-reference/', import.meta.url)

test('Blender subway reference template wires the canonical prompt and 16:9 preset', async () => {
  const template = JSON.parse(await readFile(templateUrl, 'utf8'))
  const brief = template.nodes.find((node) => node.id === 'scene-brief')
  const build = template.nodes.find((node) => node.id === 'build-scene')

  assert.equal(template.templateId, 'blender-subway-reference')
  assert.deepEqual(template.requires, ['blender-scene'])
  assert.equal(build?.data?.nodePackId, 'blender-scene/build')
  assert.equal(build?.data?.params?.preset, 'subway_station')
  assert.equal(build?.data?.params?.render_width / build?.data?.params?.render_height, 16 / 9)
  assert.equal(build?.data?.params?.tactile_width_ratio, 0.16)
  assert.equal(build?.data?.params?.tactile_inset_ratio, 0.04)
  assert.match(brief?.data?.params?.text ?? '', /foreground column.*right/i)
  assert.match(brief?.data?.params?.text ?? '', /yellow .*tactile strips?/i)
  assert.match(brief?.data?.params?.text ?? '', /flush with the platform slabs/i)
  assert.match(brief?.data?.params?.text ?? '', /open platform edges remain unobstructed/i)
  assert.match(brief?.data?.params?.text ?? '', /continuous platform.*tiled wall.*track/i)
  assert.doesNotMatch(brief?.data?.params?.text ?? '', /real portal.*connected side corridor/i)
  assert.match(brief?.data?.params?.text ?? '', /recessed grooves.*LED beads.*transparent glass diffuser/i)
  assert.match(brief?.data?.params?.text ?? '', /deep shadowed tunnel/i)
  assert.deepEqual(
    template.edges.map((edge) => [edge.source, edge.target, edge.targetHandle ?? null]),
    [
      ['scene-brief', 'build-scene', 'input-0'],
      ['build-scene', 'scene-output', null],
    ],
  )
})

test('Blender subway reference example includes the rendered image and editable blend', async () => {
  const [entry, blend] = await Promise.all([
    stat(new URL('./entry.png', exampleDir)),
    stat(new URL('./subway_station_reference.blend', exampleDir)),
  ])
  assert.ok(entry.size > 1000)
  assert.ok(blend.size > 1000)
})

import assert from 'node:assert/strict'
import fs from 'node:fs'
import test from 'node:test'

const worldCanvas = fs.readFileSync(new URL('./components/WorldCanvas.tsx', import.meta.url), 'utf8')

test('world canvas remains a Blender artifact loader, not a browser modeler', () => {
  assert.doesNotMatch(worldCanvas, /buildProceduralGeometry|proceduralMaterial|TerrainGrass|PlaneGeometry/)
  assert.match(worldCanvas, /workspacePath = artifacts\[asset\.id\]\?\.mesh\?\.workspace_path/)
  assert.match(worldCanvas, /if \(!workspacePath\) return null/)
})

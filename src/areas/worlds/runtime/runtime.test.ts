import assert from 'node:assert/strict'
import test from 'node:test'
import * as THREE from 'three'

import { DEMO_SPEC } from './demo.ts'
import { buildTerrainGrass, disposeTerrainGrass } from './grassField.ts'
import { Noise2D } from './noise.ts'
import { solvePlacements } from './placement.ts'
import { buildProceduralGeometry } from './procedural.ts'
import { hashString, mulberry32 } from './rng.ts'
import { createInitialRuntime, currentRuntimeStage, WORLD_RUNTIME_STAGE_IDS } from './runtime.ts'
import { buildTerrain } from './terrain.ts'

test('world runtime starts spec-first with locked downstream passes', () => {
  const runtime = createInitialRuntime('Build a playable winter cabin demo')
  assert.equal(runtime.version, 1)
  assert.equal(runtime.intent.prompt, 'Build a playable winter cabin demo')
  assert.equal(runtime.build, null)
  assert.equal(runtime.scene, null)
  assert.deepEqual(runtime.compiled.instances, [])
  assert.deepEqual(runtime.state.stages.map((stage) => stage.id), WORLD_RUNTIME_STAGE_IDS)
  assert.equal(runtime.state.stages[0].status, 'ready')
  assert.ok(runtime.state.stages.slice(1).every((stage) => stage.status === 'locked'))
  assert.equal(currentRuntimeStage(runtime.state)?.id, 'intent')
  assert.equal(runtime.state.gates.construction.status, 'pending')
  assert.equal(runtime.game.player.controller, 'walk')
})

test('seeded random and noise streams are reproducible and independent', () => {
  const first = mulberry32(1234)
  const second = mulberry32(1234)
  assert.deepEqual([first(), first(), first()], [second(), second(), second()])
  assert.notEqual(hashString('forest'), hashString('desert'))
  const a = new Noise2D(99)
  const b = new Noise2D(99)
  assert.deepEqual([a.sample(1.25, -2.5), a.fbm(0.2, 0.8), a.ridged(2, 3)], [b.sample(1.25, -2.5), b.fbm(0.2, 0.8), b.ridged(2, 3)])
})

test('demo terrain is local, bounded, and deterministic', () => {
  const options = { resolution: 48 }
  const first = buildTerrain(DEMO_SPEC, options)
  const second = buildTerrain(DEMO_SPEC, options)
  assert.equal(first.res, 48)
  assert.deepEqual(first.heights, second.heights)
  assert.ok(first.minHeight < first.maxHeight)
  assert.equal(first.heights.length, 48 * 48)
  assert.ok(first.slopeAt(0, 0) >= 0)
  assert.ok(first.regionWeightAt(999, 0, 0) === 0)
  assert.ok(Number.isFinite(first.waterDistanceAt(0, 0)))
})

test('terrain grass is deterministic, terrain-aligned, and capped', () => {
  const terrain = buildTerrain(DEMO_SPEC, { resolution: 32 })
  const first = buildTerrainGrass(DEMO_SPEC, terrain, { density: 1, maxBlades: 240 })
  const second = buildTerrainGrass(DEMO_SPEC, terrain, { density: 1, maxBlades: 240 })
  const firstMatrix = new Float32Array(first.mesh.instanceMatrix.array)
  const secondMatrix = new Float32Array(second.mesh.instanceMatrix.array)

  assert.ok(first.bladeCount > 0)
  assert.ok(first.bladeCount <= 240)
  assert.deepEqual(firstMatrix, secondMatrix)
  assert.equal(first.mesh.instanceColor?.array.length, second.mesh.instanceColor?.array.length)

  const matrix = new THREE.Matrix4()
  const position = new THREE.Vector3()
  for (let index = 0; index < first.bladeCount; index += 1) {
    first.mesh.getMatrixAt(index, matrix)
    position.setFromMatrixPosition(matrix)
    assert.ok(position.y > DEMO_SPEC.seaLevel)
  }

  disposeTerrainGrass(first)
  disposeTerrainGrass(second)
})

test('placement output is deterministic and terrain aligned', () => {
  const terrain = buildTerrain(DEMO_SPEC, { resolution: 64 })
  const first = solvePlacements(DEMO_SPEC, terrain)
  const second = solvePlacements(DEMO_SPEC, terrain)
  assert.deepEqual(first, second)
  assert.ok(first.length > 0)
  assert.ok(first.every((instance) => Number.isFinite(instance.position[1]) && Number.isFinite(instance.scale)))
  assert.ok(new Set(first.map((instance) => instance.id)).size === first.length)
})

test('procedural prototypes are normalized and deterministic', () => {
  const first = buildProceduralGeometry('pine', 'demo')
  const second = buildProceduralGeometry('pine', 'demo')
  first.computeBoundingBox()
  second.computeBoundingBox()
  assert.ok(first.boundingBox)
  assert.equal(first.boundingBox?.min.y, 0)
  assert.ok(Math.abs((first.boundingBox?.max.y ?? 0) - 1) < 1e-6)
  assert.deepEqual(first.attributes.position.array, second.attributes.position.array)
  first.dispose()
  second.dispose()
})

import assert from 'node:assert/strict'
import test from 'node:test'

import { DEMO_SPEC } from './demo.ts'
import { Noise2D } from './noise.ts'
import { solvePlacements } from './placement.ts'
import { buildProceduralGeometry } from './procedural.ts'
import { hashString, mulberry32 } from './rng.ts'
import { buildTerrain } from './terrain.ts'

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


/** Deterministic, terrain-aware placement of prototype instances. */

import { clamp } from './noise'
import { gaussian, subRng, type Rng } from './rng'
import type { BuiltTerrain } from './terrain'
import type {
  AssetProtoSpec,
  Instance,
  PlacementRule,
  UV,
  WorldSpec,
} from './types'

interface Candidate {
  x: number
  z: number
  yaw: number
}

interface Occupied {
  x: number
  z: number
  radius: number
}

export const MAX_PLACEMENT_ATTEMPTS = 40

/**
 * Resolve all prototype rules in spec order. The seed stream is independent
 * per prototype, so changing one asset's count does not reshuffle the others.
 */
export function solvePlacements(spec: WorldSpec, terrain: BuiltTerrain): Instance[] {
  const instances: Instance[] = []
  const occupied: Occupied[] = []
  for (const prototype of spec.assets) {
    const random = subRng(spec.seed, `place:${prototype.id}`)
    let successfulOrdinal = 0
    for (const rule of prototype.placements) {
      const regionIndices = rule.regions
        .map((id) => spec.regions.findIndex((region) => region.id === id))
        .filter((index) => index >= 0)
      for (let ordinal = 0; ordinal < Math.max(0, Math.floor(rule.count)); ordinal += 1) {
        const placed = placeOne(spec, terrain, prototype, rule, regionIndices, random, ordinal, occupied)
        if (!placed) continue
        placed.id = `${prototype.id}:${successfulOrdinal}`
        successfulOrdinal += 1
        instances.push(placed)
      }
    }
  }
  return instances
}

function placeOne(
  spec: WorldSpec,
  terrain: BuiltTerrain,
  prototype: AssetProtoSpec,
  rule: PlacementRule,
  regionIndices: number[],
  random: Rng,
  ordinal: number,
  occupied: Occupied[],
): Instance | null {
  const halfSize = spec.size / 2
  const anchor = resolveAnchor(spec, rule, regionIndices)
  for (let attempt = 0; attempt < MAX_PLACEMENT_ATTEMPTS; attempt += 1) {
    const candidate = proposeCandidate(spec, terrain, rule, regionIndices, anchor, random, ordinal, attempt)
    if (!candidate || Math.abs(candidate.x) > halfSize * 0.96 || Math.abs(candidate.z) > halfSize * 0.96) continue
    const terrainHeight = terrain.heightAt(candidate.x, candidate.z)
    const underwater = terrainHeight <= spec.seaLevel + 0.3
    if (rule.arrangement === 'waterfront') {
      if (underwater || terrain.waterDistanceAt(candidate.x, candidate.z) > Math.max(rule.spacing * 2, 24)) continue
    } else if (underwater) {
      continue
    }
    if (terrain.slopeAt(candidate.x, candidate.z) > rule.maxSlope) continue
    if (regionIndices.length > 0) {
      const regionWeight = Math.max(...regionIndices.map((index) => terrain.regionWeightAt(index, candidate.x, candidate.z)))
      if (regionWeight < 0.35) continue
    }

    const spacing = Math.max(0.5, rule.spacing)
    let collides = false
    for (const existing of occupied) {
      const minimumDistance = (spacing + existing.radius) * 0.5
      const dx = existing.x - candidate.x
      const dz = existing.z - candidate.z
      if (dx * dx + dz * dz < minimumDistance * minimumDistance) {
        collides = true
        break
      }
    }
    if (collides) continue

    occupied.push({ x: candidate.x, z: candidate.z, radius: spacing })
    const scaleJitter = Number.isFinite(rule.scaleJitter) ? rule.scaleJitter : 0
    const scale = Math.max(0.01, 1 + (random() * 2 - 1) * scaleJitter)
    let tiltX = 0
    let tiltZ = 0
    if (prototype.category === 'rock' || prototype.category === 'vegetation') {
      const [normalX, , normalZ] = terrain.normalAt(candidate.x, candidate.z)
      tiltX = clamp(normalZ * 0.8, -0.35, 0.35)
      tiltZ = clamp(-normalX * 0.8, -0.35, 0.35)
    }
    const sink = prototype.category === 'rock' ? 0.35 : 0.06
    return {
      id: '',
      protoId: prototype.id,
      position: [candidate.x, terrainHeight - sink * prototype.targetHeight * scale * 0.2, candidate.z],
      rotation: [tiltX, candidate.yaw, tiltZ],
      scale,
      regionId: dominantRegionId(spec, terrain, candidate.x, candidate.z),
    }
  }
  return null
}

function resolveAnchor(spec: WorldSpec, rule: PlacementRule, regionIndices: number[]): UV {
  if (rule.anchor) return rule.anchor
  if (regionIndices.length > 0) return spec.regions[regionIndices[0]].center
  return [0.5, 0.5]
}

function proposeCandidate(
  spec: WorldSpec,
  terrain: BuiltTerrain,
  rule: PlacementRule,
  regionIndices: number[],
  anchor: UV,
  random: Rng,
  ordinal: number,
  attempt: number,
): Candidate | null {
  const radius = regionIndices.length > 0 ? Math.max(0.01, spec.regions[regionIndices[0]].radius) : 0.35
  const toWorld = (u: number, v: number): [number, number] => [(u - 0.5) * spec.size, (v - 0.5) * spec.size]
  switch (rule.arrangement) {
    case 'cluster': {
      const standardDeviation = radius * 0.28
      const u = clamp(anchor[0] + gaussian(random, 0, standardDeviation), 0.02, 0.98)
      const v = clamp(anchor[1] + gaussian(random, 0, standardDeviation), 0.02, 0.98)
      const [x, z] = toWorld(u, v)
      return { x, z, yaw: random() * Math.PI * 2 }
    }
    case 'ring': {
      const angle = ordinal / Math.max(rule.count, 1) * Math.PI * 2 + random() * 0.3
      const ringRadius = radius * (0.75 + random() * 0.15)
      const u = clamp(anchor[0] + Math.cos(angle) * ringRadius, 0.02, 0.98)
      const v = clamp(anchor[1] + Math.sin(angle) * ringRadius, 0.02, 0.98)
      const [x, z] = toWorld(u, v)
      const yaw = rule.faceAlong ? Math.atan2(anchor[0] - u, anchor[1] - v) : random() * Math.PI * 2
      return { x, z, yaw }
    }
    case 'row': {
      const rowDirection = subRng(spec.seed, `row:${rule.regions.join(',')}`)() * Math.PI * 2
      const t = (ordinal / Math.max(rule.count - 1, 1) - 0.5) * radius * 1.4
      const offset = (random() - 0.5) * radius * 0.12
      const u = clamp(anchor[0] + Math.cos(rowDirection) * t - Math.sin(rowDirection) * offset, 0.02, 0.98)
      const v = clamp(anchor[1] + Math.sin(rowDirection) * t + Math.cos(rowDirection) * offset, 0.02, 0.98)
      const [x, z] = toWorld(u, v)
      const yaw = rule.faceAlong ? -rowDirection + Math.PI / 2 : random() * Math.PI * 2
      return { x, z, yaw }
    }
    case 'waterfront': {
      const u = clamp(anchor[0] + (random() - 0.5) * radius * 2.2, 0.02, 0.98)
      const v = clamp(anchor[1] + (random() - 0.5) * radius * 2.2, 0.02, 0.98)
      let [x, z] = toWorld(u, v)
      for (let step = 0; step < 24; step += 1) {
        if (terrain.heightAt(x, z) <= spec.seaLevel) break
        const [normalX, , normalZ] = terrain.normalAt(x, z)
        x += normalX * terrain.cellSize * 3
        z += normalZ * terrain.cellSize * 3
      }
      for (let step = 0; step < 12 && terrain.heightAt(x, z) <= spec.seaLevel + 0.4; step += 1) {
        const [normalX, , normalZ] = terrain.normalAt(x, z)
        x -= normalX * terrain.cellSize * 1.5
        z -= normalZ * terrain.cellSize * 1.5
      }
      return { x, z, yaw: rule.faceAlong ? faceWater(terrain, x, z) : random() * Math.PI * 2 }
    }
    case 'scatter':
    default: {
      const spread = radius * (1.1 + attempt * 0.02)
      const u = clamp(anchor[0] + (random() * 2 - 1) * spread, 0.02, 0.98)
      const v = clamp(anchor[1] + (random() * 2 - 1) * spread, 0.02, 0.98)
      const [x, z] = toWorld(u, v)
      return { x, z, yaw: random() * Math.PI * 2 }
    }
  }
}

function faceWater(terrain: BuiltTerrain, x: number, z: number): number {
  const [normalX, , normalZ] = terrain.normalAt(x, z)
  return Math.atan2(normalX, normalZ)
}

function dominantRegionId(spec: WorldSpec, terrain: BuiltTerrain, x: number, z: number): string | null {
  let bestIndex = -1
  let bestWeight = 0.15
  for (let index = 0; index < spec.regions.length; index += 1) {
    const weight = terrain.regionWeightAt(index, x, z)
    if (weight > bestWeight) {
      bestWeight = weight
      bestIndex = index
    }
  }
  return bestIndex >= 0 ? spec.regions[bestIndex].id : null
}

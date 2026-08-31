/**
 * Deterministic WorldSpec -> heightfield compiler.
 *
 * The result is a compact, renderer-agnostic planning representation. A
 * Blender terrain workflow or another approved backend can consume the same
 * samples without making another generation request.
 */

import { clamp, lerp, Noise2D, smoothstep } from './noise'
import { hashString } from './rng'
import type { RegionSpec, RiverSpec, WorldSpec } from './types'

export const TERRAIN_RES = 256

export interface TerrainBuildOptions {
  /** Grid width/height. Lower values are useful for previews and tests. */
  resolution?: number
}

export interface BuiltTerrain {
  spec: WorldSpec
  res: number
  /** Row-major heights, with v/z as the major axis. */
  heights: Float32Array
  /** Per-cell region weights in spec.regions order. */
  regionWeights: Float32Array[]
  /** Dominant region per cell, or -1 where no region has enough weight. */
  dominant: Int16Array
  cellSize: number
  minHeight: number
  maxHeight: number
  heightAt(x: number, z: number): number
  normalAt(x: number, z: number): [number, number, number]
  slopeAt(x: number, z: number): number
  regionWeightAt(regionIndex: number, x: number, z: number): number
  /** Approximate distance to the nearest sea cell or river centerline. */
  waterDistanceAt(x: number, z: number): number
}

interface RegionField {
  spec: RegionSpec
  terrainNoise: Noise2D
  detailNoise: Noise2D
}

function regionElevation(field: RegionField, u: number, v: number, normalizedDistance: number): number {
  const { spec, terrainNoise, detailNoise } = field
  const frequency = 3 / Math.max(spec.radius, 0.03)
  const nx = u * frequency
  const ny = v * frequency
  const roughness = clamp(spec.roughness, 0, 1)
  const octaves = 4 + Math.round(roughness * 3)
  const gain = 0.42 + roughness * 0.22
  let elevation: number

  switch (spec.kind) {
    case 'mountain':
    case 'snow': {
      const ridge = terrainNoise.ridged(nx * 0.9, ny * 0.9, octaves, 2.15, gain)
      elevation = Math.pow(ridge, 1.35) * spec.amplitude * (spec.kind === 'snow' ? 0.75 : 1)
      break
    }
    case 'volcanic': {
      const cone = Math.pow(1 - normalizedDistance, 1.6) * spec.amplitude
      const crater = smoothstep(0.16, 0.02, normalizedDistance) * spec.amplitude * 0.45
      const crags = terrainNoise.ridged(nx, ny, octaves, 2.2, gain) * spec.amplitude * 0.18
      elevation = cone - crater + crags
      break
    }
    case 'canyon':
    case 'mesa': {
      const base = terrainNoise.fbm(nx * 0.5, ny * 0.5, 4) * 0.5 + 0.5
      const terraceCount = Math.max(1, spec.terraces ?? (spec.kind === 'canyon' ? 5 : 3))
      const quantized = base * terraceCount
      const step = Math.floor(quantized)
      const fraction = quantized - step
      elevation = (step + smoothstep(0.35, 0.65, fraction)) / terraceCount * spec.amplitude
      break
    }
    case 'dunes':
    case 'desert': {
      const dune = terrainNoise.billow(nx * 0.5 + ny * 0.22, ny * 1.4, 4, 2, 0.5)
      const flat = terrainNoise.fbm(nx * 0.4, ny * 0.4, 3) * 0.2
      elevation = (spec.kind === 'dunes' ? dune : dune * 0.45 + flat) * spec.amplitude
      break
    }
    case 'hills':
    case 'forest': {
      const rolling = terrainNoise.billow(nx * 0.7, ny * 0.7, 4, 2, 0.5)
      elevation = rolling * spec.amplitude * (spec.kind === 'forest' ? 0.8 : 1)
      break
    }
    case 'water':
      elevation = -Math.pow(1 - normalizedDistance, 1.4) * spec.amplitude
      break
    case 'swamp': {
      const pools = terrainNoise.billow(nx * 1.2, ny * 1.2, 3)
      elevation = (0.15 - pools * 0.5) * spec.amplitude * 0.4
      break
    }
    case 'beach':
      elevation = terrainNoise.fbm(nx * 0.5, ny * 0.5, 3) * spec.amplitude * 0.2
      break
    case 'plains':
    default:
      elevation = (terrainNoise.fbm(nx * 0.6, ny * 0.6, 4) * 0.5 + 0.1) * spec.amplitude * 0.5
      break
  }

  elevation += detailNoise.fbm(nx * 4, ny * 4, 3) * spec.amplitude * 0.05 * (0.4 + roughness)
  if (spec.terraces && spec.kind !== 'canyon' && spec.kind !== 'mesa') {
    const stepHeight = spec.amplitude / Math.max(1, spec.terraces)
    const quantized = elevation / stepHeight
    const step = Math.floor(quantized)
    elevation = (step + smoothstep(0.3, 0.7, quantized - step)) * stepHeight
  }
  return spec.baseElevation + elevation
}

function validResolution(requested: number | undefined): number {
  if (requested === undefined || !Number.isFinite(requested)) return TERRAIN_RES
  return Math.max(2, Math.min(1024, Math.floor(requested)))
}

export function buildTerrain(spec: WorldSpec, options: TerrainBuildOptions = {}): BuiltTerrain {
  const res = validResolution(options.resolution)
  const size = Math.max(0.001, spec.size)
  const cellSize = size / (res - 1)
  const heights = new Float32Array(res * res)
  const dominant = new Int16Array(res * res).fill(-1)
  const regionWeights = spec.regions.map(() => new Float32Array(res * res))
  const warpNoise = new Noise2D(spec.seed ^ 0x5eed)
  const backgroundNoise = new Noise2D(spec.seed ^ 0xba5e)
  const fields: RegionField[] = spec.regions.map((region) => ({
    spec: region,
    terrainNoise: new Noise2D((spec.seed ^ hashString(region.id)) >>> 0),
    detailNoise: new Noise2D((spec.seed ^ hashString(`${region.id}:detail`)) >>> 0),
  }))

  for (let j = 0; j < res; j += 1) {
    const v = j / (res - 1)
    for (let i = 0; i < res; i += 1) {
      const u = i / (res - 1)
      const index = j * res + i
      let totalWeight = 0
      let height = 0
      let bestRegion = -1
      let bestWeight = 0

      for (let regionIndex = 0; regionIndex < fields.length; regionIndex += 1) {
        const field = fields[regionIndex]
        const region = field.spec
        const warpStrength = clamp(region.irregularity, 0, 1) * Math.max(0, region.radius) * 0.9
        const queryX = u * 3 + regionIndex * 7.31
        const queryY = v * 3 - regionIndex * 4.17
        const [warpedX, warpedY] = warpNoise.warp(queryX, queryY, 1)
        const du = u - region.center[0] + (warpedX - queryX) * warpStrength * 0.33
        const dv = v - region.center[1] + (warpedY - queryY) * warpStrength * 0.33
        const normalizedDistance = Math.hypot(du, dv) / Math.max(region.radius, 1e-4)
        const weight = 1 - smoothstep(0.55, 1.15, normalizedDistance)
        if (weight <= 0.001) continue
        regionWeights[regionIndex][index] = weight
        totalWeight += weight
        height += regionElevation(field, u, v, clamp(normalizedDistance, 0, 1)) * weight
        if (weight > bestWeight) {
          bestWeight = weight
          bestRegion = regionIndex
        }
      }

      const backgroundWeight = Math.max(0, 1 - totalWeight)
      if (backgroundWeight > 0) {
        const background = (backgroundNoise.fbm(u * 4, v * 4, 4) * 0.5 + 0.15) * 8
        height += background * backgroundWeight
        totalWeight += backgroundWeight
      }
      const micro = backgroundNoise.fbm(u * 34 + 11.3, v * 34 - 5.7, 3) * 1.1
        + backgroundNoise.fbm(u * 90 - 2.1, v * 90 + 8.4, 2) * 0.3
      heights[index] = height / Math.max(totalWeight, 1e-6) + micro
      if (bestWeight > 0.25) dominant[index] = bestRegion
    }
  }

  // Splat-friendly weights: overlaps preserve their relative proportions.
  for (let index = 0; index < heights.length; index += 1) {
    let total = 0
    for (const weights of regionWeights) total += weights[index]
    if (total > 1) {
      for (const weights of regionWeights) weights[index] /= total
    }
  }

  // Lower the boundary into the sea to avoid an artificial vertical wall.
  for (let j = 0; j < res; j += 1) {
    const v = j / (res - 1)
    for (let i = 0; i < res; i += 1) {
      const u = i / (res - 1)
      const edgeDistance = Math.min(u, v, 1 - u, 1 - v)
      const falloff = smoothstep(0, 0.08, edgeDistance)
      const index = j * res + i
      heights[index] = lerp(spec.seaLevel - 9, heights[index], falloff)
    }
  }

  const riverDistance = carveRivers(spec.rivers, heights, res, size, spec.seaLevel, warpNoise)
  let minHeight = Infinity
  let maxHeight = -Infinity
  for (const height of heights) {
    minHeight = Math.min(minHeight, height)
    maxHeight = Math.max(maxHeight, height)
  }

  const worldToGrid = (x: number, z: number): [number, number] => [
    clamp((x / size + 0.5) * (res - 1), 0, res - 1),
    clamp((z / size + 0.5) * (res - 1), 0, res - 1),
  ]
  const sampleGrid = (values: Float32Array, gridX: number, gridZ: number): number => {
    const x0 = Math.floor(gridX)
    const z0 = Math.floor(gridZ)
    const x1 = Math.min(x0 + 1, res - 1)
    const z1 = Math.min(z0 + 1, res - 1)
    const fx = gridX - x0
    const fz = gridZ - z0
    const lower = lerp(values[z0 * res + x0], values[z0 * res + x1], fx)
    const upper = lerp(values[z1 * res + x0], values[z1 * res + x1], fx)
    return lerp(lower, upper, fz)
  }
  const heightAt = (x: number, z: number): number => {
    const [gridX, gridZ] = worldToGrid(x, z)
    return sampleGrid(heights, gridX, gridZ)
  }
  const normalAt = (x: number, z: number): [number, number, number] => {
    const left = heightAt(x - cellSize, z)
    const right = heightAt(x + cellSize, z)
    const down = heightAt(x, z - cellSize)
    const up = heightAt(x, z + cellSize)
    const nx = left - right
    const nz = down - up
    const ny = 2 * cellSize
    const length = Math.hypot(nx, ny, nz) || 1
    return [nx / length, ny / length, nz / length]
  }
  const slopeAt = (x: number, z: number): number => (
    Math.acos(clamp(normalAt(x, z)[1], -1, 1)) * 180 / Math.PI
  )
  const regionWeightAt = (regionIndex: number, x: number, z: number): number => {
    if (regionIndex < 0 || regionIndex >= regionWeights.length) return 0
    const [gridX, gridZ] = worldToGrid(x, z)
    return sampleGrid(regionWeights[regionIndex], gridX, gridZ)
  }
  const waterDistanceAt = (x: number, z: number): number => {
    const [gridX, gridZ] = worldToGrid(x, z)
    const river = riverDistance ? sampleGrid(riverDistance, gridX, gridZ) : Infinity
    if (heightAt(x, z) <= spec.seaLevel) return 0
    let sea = Infinity
    const step = cellSize * 2
    for (let direction = 0; direction < 8; direction += 1) {
      const dx = Math.cos(direction / 8 * Math.PI * 2)
      const dz = Math.sin(direction / 8 * Math.PI * 2)
      for (let distance = step; distance < size * 0.25; distance += step) {
        if (heightAt(x + dx * distance, z + dz * distance) <= spec.seaLevel) {
          sea = Math.min(sea, distance)
          break
        }
      }
    }
    return Math.min(river, sea)
  }

  return {
    spec,
    res,
    heights,
    regionWeights,
    dominant,
    cellSize,
    minHeight,
    maxHeight,
    heightAt,
    normalAt,
    slopeAt,
    regionWeightAt,
    waterDistanceAt,
  }
}

function carveRivers(
  rivers: RiverSpec[],
  heights: Float32Array,
  res: number,
  size: number,
  seaLevel: number,
  meander: Noise2D,
): Float32Array | null {
  if (rivers.length === 0) return null
  const distances = new Float32Array(res * res).fill(Infinity)

  for (const river of rivers) {
    if (river.path.length < 2) continue
    const points: [number, number][] = []
    for (let segment = 0; segment < river.path.length - 1; segment += 1) {
      const [u0, v0] = river.path[segment]
      const [u1, v1] = river.path[segment + 1]
      const segmentLength = Math.hypot(u1 - u0, v1 - v0)
      const steps = Math.max(2, Math.ceil(segmentLength * res * 1.5))
      for (let step = 0; step <= steps; step += 1) {
        const fraction = step / steps
        const u = u0 + (u1 - u0) * fraction
        const v = v0 + (v1 - v0) * fraction
        const wobble = meander.fbm(u * 6 + 11.7, v * 6 - 3.2, 3) * 0.02 * Math.sin(fraction * Math.PI)
        const perpendicularU = -(v1 - v0) / Math.max(segmentLength, 1e-6)
        const perpendicularV = (u1 - u0) / Math.max(segmentLength, 1e-6)
        points.push([u + perpendicularU * wobble, v + perpendicularV * wobble])
      }
    }
    const influence = Math.max(0, river.width) * 2.5
    const cellsInfluence = Math.ceil(influence / size * (res - 1)) + 1
    for (const [pointU, pointV] of points) {
      const centerI = Math.round(pointU * (res - 1))
      const centerJ = Math.round(pointV * (res - 1))
      for (let offsetJ = -cellsInfluence; offsetJ <= cellsInfluence; offsetJ += 1) {
        const j = centerJ + offsetJ
        if (j < 0 || j >= res) continue
        for (let offsetI = -cellsInfluence; offsetI <= cellsInfluence; offsetI += 1) {
          const i = centerI + offsetI
          if (i < 0 || i >= res) continue
          const distance = Math.hypot(offsetI, offsetJ) / (res - 1) * size
          const index = j * res + i
          distances[index] = Math.min(distances[index], distance)
        }
      }
    }
    const floor = seaLevel - Math.max(0, river.depth)
    for (let index = 0; index < heights.length; index += 1) {
      const distance = distances[index]
      if (distance > influence) continue
      const centerWeight = 1 - smoothstep(Math.max(0, river.width) * 0.5, influence, distance)
      heights[index] = Math.min(heights[index], lerp(heights[index], floor, centerWeight))
    }
  }
  return distances
}

import { clamp, smoothstep } from './noise'
import { TERRAIN_SURFACES, type RegionSpec, type TerrainSurface } from './types'

export interface SurfaceFieldInput {
  heights: Float32Array
  regionWeights: Float32Array[]
  regionSurfaces: readonly TerrainSurface[]
  res: number
  size: number
  seaLevel: number
}

export interface SurfaceFieldResult {
  /** Per-cell weights in TERRAIN_SURFACES order. */
  surfaceWeights: Float32Array[]
  /** Dominant surface index in TERRAIN_SURFACES, or -1 for an invalid empty field. */
  dominantSurface: Int8Array
}

const LEGACY_SURFACE_BY_KIND: Record<RegionSpec['kind'], TerrainSurface> = {
  mountain: 'rock',
  hills: 'grass',
  plains: 'grass',
  desert: 'sand',
  dunes: 'sand',
  water: 'water',
  canyon: 'rock',
  volcanic: 'rock',
  snow: 'snow',
  forest: 'forest',
  swamp: 'swamp',
  beach: 'beach',
  mesa: 'rock',
}

export function surfaceForRegion(region: Pick<RegionSpec, 'kind' | 'surface'>): TerrainSurface {
  return region.surface ?? LEGACY_SURFACE_BY_KIND[region.kind]
}

function suitability(surface: TerrainSurface, altitude: number, slope: number): number {
  switch (surface) {
    case 'rock':
      return 1
    case 'snow':
      return clamp(
        smoothstep(0.35, 0.72, altitude) * (1 - 0.7 * smoothstep(38, 60, slope)),
        0,
        1,
      )
    case 'forest':
      return clamp(
        (1 - smoothstep(28, 48, slope)) * (1 - smoothstep(0.65, 0.9, altitude)),
        0,
        1,
      )
    case 'grass':
      return clamp(
        (1 - smoothstep(35, 55, slope)) * (1 - smoothstep(0.78, 0.98, altitude)),
        0,
        1,
      )
    case 'sand':
      return clamp(
        (1 - smoothstep(25, 45, slope)) * (1 - smoothstep(0.58, 0.9, altitude)),
        0,
        1,
      )
    case 'beach':
      return clamp(
        (1 - smoothstep(18, 36, slope)) * (1 - smoothstep(0.12, 0.32, altitude)),
        0,
        1,
      )
    case 'swamp':
      return clamp(
        (1 - smoothstep(12, 30, slope)) * (1 - smoothstep(0.18, 0.42, altitude)),
        0,
        1,
      )
    case 'water':
      return clamp(1 - smoothstep(0.02, 0.12, altitude), 0, 1)
  }
}

export function compileSurfaceFields(input: SurfaceFieldInput): SurfaceFieldResult {
  const { heights, regionWeights, regionSurfaces, res, size, seaLevel } = input
  if (res < 2 || heights.length !== res * res) {
    throw new Error('Surface fields require a square terrain height grid')
  }
  if (regionWeights.some((weights) => weights.length !== heights.length)) {
    throw new Error('Surface region weights must match the terrain height grid')
  }

  const surfaceWeights = TERRAIN_SURFACES.map(() => new Float32Array(heights.length))
  const dominantSurface = new Int8Array(heights.length).fill(-1)
  let maxHeight = -Infinity
  for (const height of heights) maxHeight = Math.max(maxHeight, height)
  const relief = Math.max(maxHeight - seaLevel, 1e-6)
  const cellSize = Math.max(size, 0.001) / (res - 1)
  const waterIndex = TERRAIN_SURFACES.indexOf('water')
  const rockIndex = TERRAIN_SURFACES.indexOf('rock')
  const grassIndex = TERRAIN_SURFACES.indexOf('grass')

  const gridHeight = (column: number, row: number): number => {
    const safeColumn = Math.max(0, Math.min(res - 1, column))
    const safeRow = Math.max(0, Math.min(res - 1, row))
    return heights[safeRow * res + safeColumn]
  }

  for (let row = 0; row < res; row += 1) {
    for (let column = 0; column < res; column += 1) {
      const index = row * res + column
      const height = heights[index]
      if (height <= seaLevel) {
        surfaceWeights[waterIndex][index] = 1
        dominantSurface[index] = waterIndex
        continue
      }

      const left = gridHeight(column - 1, row)
      const right = gridHeight(column + 1, row)
      const down = gridHeight(column, row - 1)
      const up = gridHeight(column, row + 1)
      const nx = left - right
      const nz = down - up
      const ny = 2 * cellSize
      const length = Math.hypot(nx, ny, nz) || 1
      const slope = Math.acos(clamp(ny / length, -1, 1)) * 180 / Math.PI
      const altitude = clamp((height - seaLevel) / relief, 0, 1)
      const accumulated = new Float64Array(TERRAIN_SURFACES.length)
      let totalRegionWeight = 0

      for (let regionIndex = 0; regionIndex < regionWeights.length; regionIndex += 1) {
        const weight = clamp(regionWeights[regionIndex][index], 0, 1)
        if (weight <= 0) continue
        totalRegionWeight += weight
        const surface = regionSurfaces[regionIndex] ?? 'rock'
        const surfaceIndex = TERRAIN_SURFACES.indexOf(surface)
        const fit = suitability(surface, altitude, slope)
        if (surface === 'rock') {
          accumulated[rockIndex] += weight
        } else {
          const primary = weight * fit
          accumulated[surfaceIndex] += primary
          accumulated[rockIndex] += weight - primary
        }
      }

      const backgroundWeight = Math.max(0, 1 - totalRegionWeight)
      const backgroundRock = clamp(
        0.15
          + 0.65 * smoothstep(18, 45, slope)
          + 0.2 * smoothstep(0.6, 0.9, altitude),
        0,
        1,
      )
      accumulated[rockIndex] += backgroundWeight * backgroundRock
      accumulated[grassIndex] += backgroundWeight * (1 - backgroundRock)

      let total = 0
      for (const weight of accumulated) total += weight
      if (total <= 1e-9) {
        accumulated[grassIndex] = 1
        total = 1
      }

      let bestIndex = -1
      let bestWeight = -1
      for (let surfaceIndex = 0; surfaceIndex < accumulated.length; surfaceIndex += 1) {
        const weight = accumulated[surfaceIndex] / total
        surfaceWeights[surfaceIndex][index] = weight
        if (weight > bestWeight) {
          bestWeight = weight
          bestIndex = surfaceIndex
        }
      }
      dominantSurface[index] = bestIndex
    }
  }

  return { surfaceWeights, dominantSurface }
}

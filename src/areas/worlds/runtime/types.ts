/**
 * Serializable world vocabulary shared by the local planner and renderer.
 *
 * This module deliberately contains no renderer or network types. A
 * WorldSpec can be saved as JSON and rebuilt entirely on the client.
 */

export const TERRAIN_KINDS = [
  'mountain',
  'hills',
  'plains',
  'desert',
  'dunes',
  'water',
  'canyon',
  'volcanic',
  'snow',
  'forest',
  'swamp',
  'beach',
  'mesa',
] as const

export type TerrainKind = typeof TERRAIN_KINDS[number]

/** Normalized square-map coordinates. u points east (+x), v points south (+z). */
export type UV = [number, number]

export interface MaterialSpec {
  name: string
  texturePrompt: string
  color: string
  tiling: number
}

export interface RegionSpec {
  id: string
  name: string
  kind: TerrainKind
  center: UV
  radius: number
  irregularity: number
  baseElevation: number
  amplitude: number
  roughness: number
  terraces?: number
  material: MaterialSpec
}

export interface RiverSpec {
  id: string
  path: UV[]
  width: number
  depth: number
}

export interface SkySpec {
  panoramaPrompt: string
  sunAzimuth: number
  sunElevation: number
  sunColor: string
  sunIntensity: number
  ambientColor: string
  ambientIntensity: number
  fogColor: string
  fogDensity: number
}

export type Arrangement = 'scatter' | 'cluster' | 'row' | 'ring' | 'waterfront'

export interface PlacementRule {
  regions: string[]
  count: number
  arrangement: Arrangement
  anchor?: UV
  spacing: number
  maxSlope: number
  scaleJitter: number
  faceAlong?: boolean
}

export type ProceduralHint =
  | 'tree'
  | 'pine'
  | 'palm'
  | 'cactus'
  | 'rock'
  | 'boulder'
  | 'grass'
  | 'crystal'
  | 'hut'
  | 'monolith'

export interface AssetProtoSpec {
  id: string
  name: string
  category: 'structure' | 'prop' | 'creature' | 'vegetation' | 'rock'
  imagePrompt: string
  targetHeight: number
  tier: 'hero' | 'scatter'
  proceduralHint?: ProceduralHint
  placements: PlacementRule[]
}

export interface RelationSpec {
  subject: string
  relation: 'near' | 'inside' | 'away_from' | 'overlooking'
  object: string
}

export interface WorldSpec {
  name: string
  logline: string
  seed: number
  size: number
  seaLevel: number
  sky: SkySpec
  regions: RegionSpec[]
  rivers: RiverSpec[]
  assets: AssetProtoSpec[]
  relations: RelationSpec[]
}

/**
 * A newly-created scene is intentionally persisted before the Agent has
 * written its full plan. Keep that shell document out of the renderer until
 * all world fields are present.
 */
export function isRenderableWorldSpec(value: unknown): value is WorldSpec {
  if (!value || typeof value !== 'object') return false
  const spec = value as Partial<WorldSpec>
  return typeof spec.name === 'string'
    && typeof spec.logline === 'string'
    && typeof spec.seed === 'number'
    && typeof spec.size === 'number'
    && typeof spec.seaLevel === 'number'
    && Boolean(spec.sky)
    && Array.isArray(spec.regions)
    && Array.isArray(spec.rivers)
    && Array.isArray(spec.assets)
    && Array.isArray(spec.relations)
}

export interface Instance {
  id: string
  protoId: string
  position: [number, number, number]
  rotation: [number, number, number]
  scale: number
  regionId: string | null
}

/** Optional metadata a future local workflow may attach to a generated asset. */
export interface AssetArtifact {
  protoId: string
  imageUrl?: string
  meshUrl?: string
  procedural: boolean
}

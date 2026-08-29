/**
 * Deterministic, stylized grass field built from a BuiltTerrain heightfield.
 *
 * The world compiler remains the source of truth. This module only turns its
 * samples into one instanced render mesh; it does not create or persist new
 * world data.
 */

import * as THREE from 'three'

import { clamp, smoothstep } from './noise'
import { subRng, type Rng } from './rng'
import type { BuiltTerrain } from './terrain'
import type { TerrainKind, WorldSpec } from './types'

export interface TerrainGrassOptions {
  /** Blades per square world unit before the maximum cap is applied. */
  density?: number
  /** Safety cap for a single instanced mesh. */
  maxBlades?: number
  seed?: number
  /** Wind angle in degrees, measured on the world XZ plane. */
  windDirection?: number
  windStrength?: number
}

export interface TerrainGrassField {
  mesh: THREE.InstancedMesh<THREE.BufferGeometry, THREE.ShaderMaterial>
  bladeCount: number
}

const DEFAULT_DENSITY = 8
const DEFAULT_MAX_BLADES = 24_000
const DEFAULT_WIND_STRENGTH = 0.16
const DEFAULT_WIND_DIRECTION = 28
const GRASS_SEGMENTS = 3
const GRASS_KINDS: ReadonlySet<TerrainKind> = new Set([
  'plains',
  'forest',
  'hills',
  'swamp',
  'beach',
])

const GRASS_COLORS: Record<TerrainKind, string> = {
  plains: '#78a84a',
  forest: '#4f8249',
  hills: '#6d9a4c',
  swamp: '#557b43',
  beach: '#8da85a',
  mountain: '#6f8a53',
  desert: '#8da85a',
  dunes: '#8da85a',
  water: '#78a84a',
  canyon: '#78a84a',
  volcanic: '#78a84a',
  snow: '#78a84a',
  mesa: '#78a84a',
}

const GRASS_VERTEX_SHADER = /* glsl */ `
  attribute vec3 instanceColor;

  uniform float uTime;
  uniform float uWindStrength;
  uniform float uWindSpeed;
  uniform float uWindFrequency;
  uniform float uWindTurbulence;
  uniform vec2 uWindDirection;

  varying float vBladeHeight;
  varying vec3 vInstanceColor;
  varying vec3 vWorldPosition;
  varying vec3 vWorldNormal;

  mat3 inverseTransposeMat3(mat3 matrix) {
    vec3 row0 = cross(matrix[1], matrix[2]);
    vec3 row1 = cross(matrix[2], matrix[0]);
    vec3 row2 = cross(matrix[0], matrix[1]);
    float determinant = dot(matrix[0], row0);
    return mat3(row0, row1, row2) / determinant;
  }

  void main() {
    mat4 instanceWorldMatrix = modelMatrix * instanceMatrix;
    vec3 baseWorldPosition = (instanceWorldMatrix * vec4(0.0, 0.0, 0.0, 1.0)).xyz;
    vec3 worldPosition = (instanceWorldMatrix * vec4(position, 1.0)).xyz;

    float bladeHeight = clamp(uv.y, 0.0, 1.0);
    float primaryWave = sin(
      dot(baseWorldPosition.xz, uWindDirection) * uWindFrequency + uTime * uWindSpeed
    );
    vec2 perpendicular = vec2(-uWindDirection.y, uWindDirection.x);
    float secondaryWave = sin(
      dot(baseWorldPosition.xz, perpendicular) * uWindFrequency * 1.7
        + uTime * uWindSpeed * 0.73
    ) * uWindTurbulence;
    float tipMask = bladeHeight * bladeHeight;
    vec3 wind = vec3(uWindDirection.x, 0.0, uWindDirection.y)
      * ((primaryWave + secondaryWave) * uWindStrength + uWindStrength * 0.22)
      * tipMask;
    worldPosition += wind;

    vBladeHeight = bladeHeight;
    vInstanceColor = instanceColor;
    vWorldPosition = worldPosition;
    vWorldNormal = normalize(inverseTransposeMat3(mat3(instanceWorldMatrix)) * normal);
    gl_Position = projectionMatrix * viewMatrix * vec4(worldPosition, 1.0);
  }
`

const GRASS_FRAGMENT_SHADER = /* glsl */ `
  uniform vec3 uGrassBottom;
  uniform vec3 uGrassTop;
  uniform vec3 uBacklightColor;
  uniform vec3 uLightDirection;
  uniform vec3 uLightColor;
  uniform float uLightIntensity;
  uniform float uBacklightStrength;
  uniform float uBrightness;

  varying float vBladeHeight;
  varying vec3 vInstanceColor;
  varying vec3 vWorldPosition;
  varying vec3 vWorldNormal;

  void main() {
    float gradient = smoothstep(0.04, 1.0, vBladeHeight);
    vec3 grassColor = mix(uGrassBottom, uGrassTop, gradient) * vInstanceColor;

    vec3 lightDirection = normalize(uLightDirection);
    float diffuse = 0.42 + 0.58 * max(dot(vec3(0.0, 1.0, 0.0), lightDirection), 0.0);
    vec3 viewDirection = normalize(cameraPosition - vWorldPosition);
    float edgeOn = 1.0 - abs(dot(normalize(vWorldNormal), lightDirection));
    float backlight = pow(max(dot(viewDirection, -lightDirection), 0.0), 3.0)
      * edgeOn * (0.25 + vBladeHeight * 0.75) * uBacklightStrength;

    vec3 lit = grassColor * diffuse * uLightColor * uLightIntensity;
    lit += uBacklightColor * uLightColor * backlight;
    gl_FragColor = vec4(lit * uBrightness, 1.0);
    #include <tonemapping_fragment>
    #include <colorspace_fragment>
  }
`

function makeBladeGeometry(segments: number, lean: number): THREE.BufferGeometry {
  const positions: number[] = []
  const uvs: number[] = []
  const indices: number[] = []

  const appendStrip = (angle: number): void => {
    const cos = Math.cos(angle)
    const sin = Math.sin(angle)
    const baseVertex = positions.length / 3
    for (let row = 0; row < segments; row += 1) {
      const t = row / segments
      const width = 0.5 * Math.pow(1 - t, 1.2)
      const bend = lean * t * t
      const leftX = -width * cos - bend * sin
      const leftZ = -width * sin + bend * cos
      const rightX = width * cos - bend * sin
      const rightZ = width * sin + bend * cos
      positions.push(leftX, t, leftZ, rightX, t, rightZ)
      uvs.push(0, t, 1, t)

      const rowVertex = baseVertex + row * 2
      if (row < segments - 1) {
        indices.push(
          rowVertex,
          rowVertex + 2,
          rowVertex + 1,
          rowVertex + 1,
          rowVertex + 2,
          rowVertex + 3,
        )
      }
    }

    const tipX = -lean * sin
    const tipZ = lean * cos
    positions.push(tipX, 1, tipZ)
    uvs.push(0.5, 1)
    const finalRow = baseVertex + (segments - 1) * 2
    const tip = baseVertex + segments * 2
    indices.push(finalRow, tip, finalRow + 1)
  }

  // Two crossed ribbons avoid the paper-thin look while remaining cheap.
  appendStrip(0)
  appendStrip(Math.PI / 2)

  const geometry = new THREE.BufferGeometry()
  geometry.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3))
  geometry.setAttribute('uv', new THREE.Float32BufferAttribute(uvs, 2))
  geometry.setIndex(indices)
  geometry.computeVertexNormals()
  geometry.computeBoundingSphere()
  return geometry
}

function grassCoverageAt(spec: WorldSpec, terrain: BuiltTerrain, x: number, z: number): {
  amount: number
  kind: TerrainKind | null
} {
  if (terrain.heightAt(x, z) <= spec.seaLevel + 0.3) return { amount: 0, kind: null }

  let amount = 0
  let dominantWeight = 0
  let dominantKind: TerrainKind | null = null
  for (let index = 0; index < spec.regions.length; index += 1) {
    const region = spec.regions[index]
    const weight = terrain.regionWeightAt(index, x, z)
    if (weight <= 0) continue
    if (GRASS_KINDS.has(region.kind)) {
      const kindDensity = region.kind === 'swamp' ? 0.45 : region.kind === 'beach' ? 0.3 : 1
      amount += weight * kindDensity
      if (weight > dominantWeight) {
        dominantWeight = weight
        dominantKind = region.kind
      }
    }
  }
  if (amount <= 0) return { amount: 0, kind: null }

  // Grass thins naturally on exposed slopes without needing a second mask.
  const slopeFactor = 1 - smoothstep(22, 42, terrain.slopeAt(x, z))
  return {
    amount: clamp(amount, 0, 1) * slopeFactor,
    kind: dominantKind,
  }
}

function variedColor(kind: TerrainKind | null, random: Rng): THREE.Color {
  const color = new THREE.Color(GRASS_COLORS[kind ?? 'plains'])
  return color.offsetHSL(
    (random() - 0.5) * 0.04,
    (random() - 0.5) * 0.1,
    (random() - 0.5) * 0.16,
  )
}

function createMaterial(
  spec: WorldSpec,
  options: Required<Pick<TerrainGrassOptions, 'windDirection' | 'windStrength'>>,
): THREE.ShaderMaterial {
  const direction = THREE.MathUtils.degToRad(options.windDirection)
  const sunAzimuth = THREE.MathUtils.degToRad(spec.sky.sunAzimuth)
  const sunElevation = THREE.MathUtils.degToRad(Math.max(8, spec.sky.sunElevation))
  return new THREE.ShaderMaterial({
    uniforms: {
      uTime: { value: 0 },
      uWindStrength: { value: options.windStrength },
      uWindSpeed: { value: 1.1 },
      uWindFrequency: { value: 0.09 },
      uWindTurbulence: { value: 0.24 },
      uWindDirection: { value: new THREE.Vector2(Math.cos(direction), Math.sin(direction)) },
      uGrassBottom: { value: new THREE.Color('#416e34') },
      uGrassTop: { value: new THREE.Color('#a7ce5a') },
      uBacklightColor: { value: new THREE.Color('#d7ed86') },
      uLightDirection: {
        value: new THREE.Vector3(
          Math.cos(sunAzimuth) * Math.cos(sunElevation),
          Math.sin(sunElevation),
          Math.sin(sunAzimuth) * Math.cos(sunElevation),
        ).normalize(),
      },
      uLightColor: { value: new THREE.Color(spec.sky.sunColor) },
      uLightIntensity: { value: spec.sky.sunIntensity },
      uBacklightStrength: { value: 1.4 },
      uBrightness: { value: 0.92 },
    },
    vertexShader: GRASS_VERTEX_SHADER,
    fragmentShader: GRASS_FRAGMENT_SHADER,
    vertexColors: true,
    side: THREE.DoubleSide,
    toneMapped: true,
    depthWrite: true,
  })
}

function validNumber(value: number | undefined, fallback: number, min: number): number {
  return value !== undefined && Number.isFinite(value) ? Math.max(min, value) : fallback
}

/** Builds the render-only grass field from deterministic terrain samples. */
export function buildTerrainGrass(
  spec: WorldSpec,
  terrain: BuiltTerrain,
  options: TerrainGrassOptions = {},
): TerrainGrassField {
  const density = validNumber(options.density, DEFAULT_DENSITY, 0)
  const maxBlades = Math.floor(validNumber(options.maxBlades, DEFAULT_MAX_BLADES, 0))
  const seed = options.seed ?? spec.seed
  const windDirection = validNumber(options.windDirection, DEFAULT_WIND_DIRECTION, -Infinity)
  const windStrength = validNumber(options.windStrength, DEFAULT_WIND_STRENGTH, 0)
  const targetCount = Math.min(maxBlades, Math.max(0, Math.floor(spec.size * spec.size * density)))
  const random = subRng(seed, 'terrain-grass')
  const instances: Array<{ position: THREE.Vector3; normal: THREE.Vector3; kind: TerrainKind | null }> = []
  const attempts = targetCount === 0 ? 0 : Math.max(256, targetCount * 10)
  const halfSize = spec.size * 0.5

  for (let attempt = 0; attempt < attempts && instances.length < targetCount; attempt += 1) {
    const x = (random() * 2 - 1) * halfSize * 0.98
    const z = (random() * 2 - 1) * halfSize * 0.98
    const coverage = grassCoverageAt(spec, terrain, x, z)
    if (coverage.amount <= 0 || random() > coverage.amount) continue
    instances.push({
      position: new THREE.Vector3(x, terrain.heightAt(x, z) + 0.015, z),
      normal: new THREE.Vector3(...terrain.normalAt(x, z)),
      kind: coverage.kind,
    })
  }

  const geometry = makeBladeGeometry(GRASS_SEGMENTS, 0.16)
  const material = createMaterial(spec, { windDirection, windStrength })
  const mesh = new THREE.InstancedMesh(geometry, material, instances.length)
  mesh.frustumCulled = false
  const rotation = new THREE.Quaternion()
  const spin = new THREE.Quaternion()
  const scale = new THREE.Vector3()
  const matrix = new THREE.Matrix4()
  const up = new THREE.Vector3(0, 1, 0)

  for (let index = 0; index < instances.length; index += 1) {
    const instance = instances[index]
    rotation.setFromUnitVectors(up, instance.normal)
    spin.setFromAxisAngle(up, random() * Math.PI * 2)
    rotation.multiply(spin)
    scale.set(
      THREE.MathUtils.lerp(0.45, 0.9, random()),
      THREE.MathUtils.lerp(0.45, 1.25, random()),
      1,
    )
    matrix.compose(instance.position, rotation, scale)
    mesh.setMatrixAt(index, matrix)
    mesh.setColorAt(index, variedColor(instance.kind, random))
  }
  mesh.instanceMatrix.needsUpdate = true
  if (mesh.instanceColor) mesh.instanceColor.needsUpdate = true
  mesh.computeBoundingSphere()

  return { mesh, bladeCount: instances.length }
}

export function disposeTerrainGrass(field: TerrainGrassField): void {
  field.mesh.geometry.dispose()
  field.mesh.material.dispose()
}

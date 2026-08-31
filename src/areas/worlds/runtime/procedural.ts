/**
 * Preview/test geometry fixture for deterministic planning experiments.
 * Production WorldCanvas never consumes this module; final meshes come from
 * Blender workspace artifacts. Every prototype is normalized to one unit tall
 * with its base at y = 0.
 */

import * as THREE from 'three'
import { mergeGeometries } from 'three/examples/jsm/utils/BufferGeometryUtils.js'
import { hashString, mulberry32, range, type Rng } from './rng'
import type { ProceduralHint } from './types'

export interface ProceduralPalette {
  trunk: string
  foliage: string
  accent: string
}

export const DEFAULT_PROCEDURAL_PALETTES: Record<ProceduralHint, ProceduralPalette> = {
  tree: { trunk: '#6b4a32', foliage: '#4f7a3a', accent: '#699a4a' },
  pine: { trunk: '#5a3f2e', foliage: '#2f5d40', accent: '#3d7050' },
  palm: { trunk: '#8a6a48', foliage: '#57904d', accent: '#6faf5f' },
  cactus: { trunk: '#4e7d46', foliage: '#5d9153', accent: '#e8d8a0' },
  rock: { trunk: '#8d8a82', foliage: '#8d8a82', accent: '#a5a29a' },
  boulder: { trunk: '#7d7a72', foliage: '#7d7a72', accent: '#96938b' },
  grass: { trunk: '#5d8a42', foliage: '#6da24c', accent: '#8abf60' },
  crystal: { trunk: '#7ec8e3', foliage: '#9fdcf0', accent: '#d7f2fb' },
  hut: { trunk: '#8a7358', foliage: '#7d4a38', accent: '#c9bda5' },
  monolith: { trunk: '#7b776d', foliage: '#8b877d', accent: '#9b978d' },
}

function withVertexColors(geometry: THREE.BufferGeometry, color: THREE.Color, jitter: number, random: Rng): THREE.BufferGeometry {
  const colors = new Float32Array(geometry.attributes.position.count * 3)
  for (let index = 0; index < geometry.attributes.position.count; index += 1) {
    const factor = 1 + (random() * 2 - 1) * jitter
    colors[index * 3] = Math.min(1, color.r * factor)
    colors[index * 3 + 1] = Math.min(1, color.g * factor)
    colors[index * 3 + 2] = Math.min(1, color.b * factor)
  }
  geometry.setAttribute('color', new THREE.BufferAttribute(colors, 3))
  return geometry
}

function displace(geometry: THREE.BufferGeometry, amount: number, random: Rng): THREE.BufferGeometry {
  const positions = geometry.attributes.position
  for (let index = 0; index < positions.count; index += 1) {
    positions.setXYZ(
      index,
      positions.getX(index) + (random() * 2 - 1) * amount,
      positions.getY(index) + (random() * 2 - 1) * amount,
      positions.getZ(index) + (random() * 2 - 1) * amount,
    )
  }
  positions.needsUpdate = true
  return geometry
}

function buildTree(random: Rng, palette: ProceduralPalette): THREE.BufferGeometry[] {
  const parts: THREE.BufferGeometry[] = []
  const trunkHeight = range(random, 0.32, 0.42)
  const trunk = new THREE.CylinderGeometry(0.028, 0.05, trunkHeight, 5, 1)
  trunk.translate(0, trunkHeight / 2, 0)
  parts.push(withVertexColors(trunk, new THREE.Color(palette.trunk), 0.08, random))
  const blobCount = 2 + Math.floor(random() * 2)
  for (let blobIndex = 0; blobIndex < blobCount; blobIndex += 1) {
    const radius = range(random, 0.2, 0.3) * (1 - blobIndex * 0.18)
    const blob = new THREE.IcosahedronGeometry(radius, 0)
    displace(blob, radius * 0.22, random)
    blob.translate(
      (random() * 2 - 1) * 0.1,
      trunkHeight + radius * 0.6 + blobIndex * radius * 0.85,
      (random() * 2 - 1) * 0.1,
    )
    parts.push(withVertexColors(blob, new THREE.Color(blobIndex % 2 ? palette.accent : palette.foliage), 0.1, random))
  }
  return parts
}

function buildPine(random: Rng, palette: ProceduralPalette): THREE.BufferGeometry[] {
  const parts: THREE.BufferGeometry[] = []
  const trunkHeight = range(random, 0.22, 0.3)
  const trunk = new THREE.CylinderGeometry(0.022, 0.045, trunkHeight, 5, 1)
  trunk.translate(0, trunkHeight / 2, 0)
  parts.push(withVertexColors(trunk, new THREE.Color(palette.trunk), 0.08, random))
  const tierCount = 3 + Math.floor(random() * 2)
  let y = trunkHeight * 0.9
  let radius = range(random, 0.24, 0.3)
  for (let tier = 0; tier < tierCount; tier += 1) {
    const height = range(random, 0.22, 0.3)
    const cone = new THREE.ConeGeometry(radius, height, 6, 1)
    displace(cone, 0.012, random)
    cone.translate((random() * 2 - 1) * 0.015, y + height / 2, (random() * 2 - 1) * 0.015)
    parts.push(withVertexColors(cone, new THREE.Color(tier % 2 ? palette.accent : palette.foliage), 0.08, random))
    y += height * 0.62
    radius *= 0.72
  }
  return parts
}

function buildPalm(random: Rng, palette: ProceduralPalette): THREE.BufferGeometry[] {
  const parts: THREE.BufferGeometry[] = []
  const segmentCount = 5
  const lean = range(random, 0.08, 0.22)
  const direction = random() * Math.PI * 2
  let x = 0
  let z = 0
  const segmentHeight = 0.16
  for (let segment = 0; segment < segmentCount; segment += 1) {
    const part = new THREE.CylinderGeometry(0.03 - segment * 0.003, 0.036 - segment * 0.003, segmentHeight, 5, 1)
    x += Math.cos(direction) * lean * segmentHeight * segment * 0.5
    z += Math.sin(direction) * lean * segmentHeight * segment * 0.5
    part.translate(x, segmentHeight * (segment + 0.5), z)
    parts.push(withVertexColors(part, new THREE.Color(palette.trunk), 0.06, random))
  }
  const topY = segmentHeight * segmentCount
  const frondCount = 5 + Math.floor(random() * 3)
  for (let frondIndex = 0; frondIndex < frondCount; frondIndex += 1) {
    const angle = frondIndex / frondCount * Math.PI * 2 + random() * 0.4
    const length = range(random, 0.3, 0.42)
    const frond = new THREE.ConeGeometry(0.045, length, 3, 1)
    frond.rotateX(Math.PI / 2)
    frond.rotateY(angle)
    const droopAxis = new THREE.Vector3(Math.cos(angle + Math.PI / 2), 0, Math.sin(angle + Math.PI / 2)).normalize()
    frond.applyMatrix4(new THREE.Matrix4().makeRotationAxis(droopAxis, -0.9))
    frond.translate(x + Math.cos(angle) * length * 0.32, topY + 0.03, z + Math.sin(angle) * length * 0.32)
    parts.push(withVertexColors(frond, new THREE.Color(frondIndex % 2 ? palette.accent : palette.foliage), 0.1, random))
  }
  return parts
}

function buildCactus(random: Rng, palette: ProceduralPalette): THREE.BufferGeometry[] {
  const parts: THREE.BufferGeometry[] = []
  const bodyHeight = range(random, 0.6, 0.85)
  const body = new THREE.CapsuleGeometry(0.09, bodyHeight, 2, 8)
  body.translate(0, bodyHeight / 2 + 0.09, 0)
  parts.push(withVertexColors(body, new THREE.Color(palette.trunk), 0.06, random))
  const armCount = Math.floor(random() * 3)
  for (let armIndex = 0; armIndex < armCount; armIndex += 1) {
    const side = random() > 0.5 ? 1 : -1
    const armHeight = range(random, 0.2, 0.34)
    const arm = new THREE.CapsuleGeometry(0.055, armHeight, 2, 6)
    arm.translate(0, armHeight / 2, 0)
    arm.rotateZ(side * -0.2)
    arm.translate(side * 0.14, bodyHeight * range(random, 0.35, 0.6), 0)
    parts.push(withVertexColors(arm, new THREE.Color(palette.foliage), 0.06, random))
  }
  return parts
}

function buildRock(random: Rng, palette: ProceduralPalette, large: boolean): THREE.BufferGeometry[] {
  const parts: THREE.BufferGeometry[] = []
  const rockCount = large ? 2 + Math.floor(random() * 2) : 1
  for (let rockIndex = 0; rockIndex < rockCount; rockIndex += 1) {
    const radius = large ? range(random, 0.3, 0.5) : range(random, 0.22, 0.35)
    const rock = new THREE.IcosahedronGeometry(radius, large ? 1 : 0)
    displace(rock, radius * 0.28, random)
    rock.scale(1, range(random, 0.55, 0.8), 1)
    rock.translate((random() * 2 - 1) * radius * 0.8, radius * 0.4, (random() * 2 - 1) * radius * 0.8)
    parts.push(withVertexColors(rock, new THREE.Color(rockIndex % 2 ? palette.accent : palette.trunk), 0.07, random))
  }
  return parts
}

function buildGrass(random: Rng, palette: ProceduralPalette): THREE.BufferGeometry[] {
  const parts: THREE.BufferGeometry[] = []
  const bladeCount = 5 + Math.floor(random() * 4)
  for (let bladeIndex = 0; bladeIndex < bladeCount; bladeIndex += 1) {
    const height = range(random, 0.16, 0.3)
    const blade = new THREE.ConeGeometry(0.016, height, 3, 1)
    const angle = random() * Math.PI * 2
    const distance = random() * 0.09
    blade.rotateZ((random() * 2 - 1) * 0.35)
    blade.translate(Math.cos(angle) * distance, height / 2, Math.sin(angle) * distance)
    parts.push(withVertexColors(blade, new THREE.Color(bladeIndex % 3 ? palette.foliage : palette.accent), 0.14, random))
  }
  return parts
}

function buildCrystal(random: Rng, palette: ProceduralPalette): THREE.BufferGeometry[] {
  const parts: THREE.BufferGeometry[] = []
  const shardCount = 3 + Math.floor(random() * 3)
  for (let shardIndex = 0; shardIndex < shardCount; shardIndex += 1) {
    const height = range(random, 0.35, 0.9) * (shardIndex === 0 ? 1 : 0.6)
    const shard = new THREE.ConeGeometry(range(random, 0.06, 0.11), height, 5, 1)
    const angle = random() * Math.PI * 2
    const distance = shardIndex === 0 ? 0 : random() * 0.14
    shard.rotateX((random() * 2 - 1) * 0.25)
    shard.rotateZ((random() * 2 - 1) * 0.25)
    shard.translate(Math.cos(angle) * distance, height * 0.42, Math.sin(angle) * distance)
    parts.push(withVertexColors(shard, new THREE.Color(shardIndex % 2 ? palette.accent : palette.foliage), 0.1, random))
  }
  return parts
}

function buildHut(random: Rng, palette: ProceduralPalette): THREE.BufferGeometry[] {
  const parts: THREE.BufferGeometry[] = []
  const width = range(random, 0.5, 0.62)
  const depth = range(random, 0.42, 0.55)
  const wallHeight = range(random, 0.3, 0.38)
  const walls = new THREE.BoxGeometry(width, wallHeight, depth)
  walls.translate(0, wallHeight / 2, 0)
  parts.push(withVertexColors(walls, new THREE.Color(palette.trunk), 0.05, random))
  const roofHeight = range(random, 0.26, 0.34)
  const roof = new THREE.ConeGeometry(Math.hypot(width, depth) * 0.62, roofHeight, 4, 1)
  roof.rotateY(Math.PI / 4)
  roof.translate(0, wallHeight + roofHeight / 2, 0)
  parts.push(withVertexColors(roof, new THREE.Color(palette.foliage), 0.06, random))
  const door = new THREE.BoxGeometry(width * 0.22, wallHeight * 0.55, 0.02)
  door.translate(0, wallHeight * 0.28, depth / 2 + 0.008)
  parts.push(withVertexColors(door, new THREE.Color(palette.accent), 0.04, random))
  if (random() > 0.4) {
    const chimney = new THREE.BoxGeometry(0.07, roofHeight + 0.12, 0.07)
    chimney.translate(width * 0.26, wallHeight + roofHeight * 0.55, depth * 0.15)
    parts.push(withVertexColors(chimney, new THREE.Color(palette.accent), 0.05, random))
  }
  return parts
}

function buildMonolith(random: Rng, palette: ProceduralPalette): THREE.BufferGeometry[] {
  const parts: THREE.BufferGeometry[] = []
  const height = range(random, 0.85, 1)
  const stone = new THREE.BoxGeometry(range(random, 0.2, 0.3), height, range(random, 0.12, 0.2), 2, 4, 2)
  displace(stone, 0.028, random)
  stone.rotateZ((random() * 2 - 1) * 0.06)
  stone.translate(0, height / 2, 0)
  parts.push(withVertexColors(stone, new THREE.Color(palette.trunk), 0.08, random))
  const base = new THREE.IcosahedronGeometry(0.16, 0)
  displace(base, 0.04, random)
  base.scale(1.4, 0.5, 1.4)
  base.translate(0, 0.05, 0)
  parts.push(withVertexColors(base, new THREE.Color(palette.foliage), 0.07, random))
  return parts
}

function buildParts(hint: ProceduralHint, random: Rng, palette: ProceduralPalette): THREE.BufferGeometry[] {
  switch (hint) {
    case 'tree': return buildTree(random, palette)
    case 'pine': return buildPine(random, palette)
    case 'palm': return buildPalm(random, palette)
    case 'cactus': return buildCactus(random, palette)
    case 'rock': return buildRock(random, palette, false)
    case 'boulder': return buildRock(random, palette, true)
    case 'grass': return buildGrass(random, palette)
    case 'crystal': return buildCrystal(random, palette)
    case 'hut': return buildHut(random, palette)
    case 'monolith': return buildMonolith(random, palette)
  }
}

/** Build a reproducible normalized prototype geometry. */
export function buildProceduralGeometry(
  hint: ProceduralHint,
  seedLabel: string,
  paletteOverride: Partial<ProceduralPalette> = {},
): THREE.BufferGeometry {
  const random = mulberry32(hashString(`proc:${hint}:${seedLabel}`))
  const palette = { ...DEFAULT_PROCEDURAL_PALETTES[hint], ...paletteOverride }
  const parts = buildParts(hint, random, palette)
  const merged = mergeGeometries(parts.map((part) => part.index ? part.toNonIndexed() : part), false)
  parts.forEach((part) => part.dispose())
  if (!merged) throw new Error(`Unable to merge procedural geometry: ${hint}`)
  merged.computeBoundingBox()
  const box = merged.boundingBox
  if (!box) throw new Error(`Procedural geometry has no bounds: ${hint}`)
  const height = Math.max(box.max.y - box.min.y, 1e-4)
  merged.translate(0, -box.min.y, 0)
  merged.scale(1 / height, 1 / height, 1 / height)
  merged.computeVertexNormals()
  merged.computeBoundingBox()
  merged.computeBoundingSphere()
  return merged
}

export function proceduralMaterial(emissive?: string): THREE.MeshStandardMaterial {
  return new THREE.MeshStandardMaterial({
    vertexColors: true,
    flatShading: true,
    roughness: 0.9,
    metalness: 0.02,
    ...(emissive ? { emissive: new THREE.Color(emissive), emissiveIntensity: 0.6 } : {}),
  })
}

/** Seeded 2D gradient noise and common fractal combinations. */

import { mulberry32 } from './rng'

const PERMUTATION_SIZE = 256

export class Noise2D {
  private readonly permutation: Uint8Array
  private readonly gradientX: Float32Array
  private readonly gradientY: Float32Array

  constructor(seed: number) {
    const random = mulberry32(seed)
    const base = new Uint8Array(PERMUTATION_SIZE)
    for (let index = 0; index < PERMUTATION_SIZE; index += 1) base[index] = index
    for (let index = PERMUTATION_SIZE - 1; index > 0; index -= 1) {
      const swapIndex = Math.floor(random() * (index + 1))
      const value = base[index]
      base[index] = base[swapIndex]
      base[swapIndex] = value
    }

    this.permutation = new Uint8Array(PERMUTATION_SIZE * 2)
    for (let index = 0; index < this.permutation.length; index += 1) {
      this.permutation[index] = base[index & (PERMUTATION_SIZE - 1)]
    }
    this.gradientX = new Float32Array(PERMUTATION_SIZE)
    this.gradientY = new Float32Array(PERMUTATION_SIZE)
    for (let index = 0; index < PERMUTATION_SIZE; index += 1) {
      const angle = index / PERMUTATION_SIZE * Math.PI * 2
      this.gradientX[index] = Math.cos(angle)
      this.gradientY[index] = Math.sin(angle)
    }
  }

  sample(x: number, y: number): number {
    const xi = Math.floor(x)
    const yi = Math.floor(y)
    const xf = x - xi
    const yf = y - yi
    const u = fade(xf)
    const v = fade(yf)
    const aa = this.permutation[(this.permutation[xi & 255] + yi) & 255]
    const ab = this.permutation[(this.permutation[xi & 255] + yi + 1) & 255]
    const ba = this.permutation[(this.permutation[(xi + 1) & 255] + yi) & 255]
    const bb = this.permutation[(this.permutation[(xi + 1) & 255] + yi + 1) & 255]
    const dot = (hash: number, dx: number, dy: number) => (
      this.gradientX[hash] * dx + this.gradientY[hash] * dy
    )
    const x0 = lerp(dot(aa, xf, yf), dot(ba, xf - 1, yf), u)
    const x1 = lerp(dot(ab, xf, yf - 1), dot(bb, xf - 1, yf - 1), u)
    return lerp(x0, x1, v) * 1.41421356
  }

  fbm(x: number, y: number, octaves = 5, lacunarity = 2, gain = 0.5): number {
    let amplitude = 1
    let frequency = 1
    let total = 0
    let normalization = 0
    for (let octave = 0; octave < Math.max(0, octaves); octave += 1) {
      total += amplitude * this.sample(x * frequency, y * frequency)
      normalization += amplitude
      amplitude *= gain
      frequency *= lacunarity
    }
    return normalization > 0 ? total / normalization : 0
  }

  ridged(x: number, y: number, octaves = 5, lacunarity = 2.1, gain = 0.5): number {
    let amplitude = 0.5
    let frequency = 1
    let total = 0
    let previous = 1
    for (let octave = 0; octave < Math.max(0, octaves); octave += 1) {
      const ridge = 1 - Math.abs(this.sample(x * frequency, y * frequency))
      const sharp = ridge * ridge
      total += sharp * amplitude * previous
      previous = sharp
      amplitude *= gain
      frequency *= lacunarity
    }
    return total
  }

  billow(x: number, y: number, octaves = 4, lacunarity = 2, gain = 0.5): number {
    let amplitude = 1
    let frequency = 1
    let total = 0
    let normalization = 0
    for (let octave = 0; octave < Math.max(0, octaves); octave += 1) {
      total += amplitude * Math.abs(this.sample(x * frequency, y * frequency))
      normalization += amplitude
      amplitude *= gain
      frequency *= lacunarity
    }
    return normalization > 0 ? total / normalization : 0
  }

  warp(x: number, y: number, strength: number): [number, number] {
    const offsetX = this.fbm(x + 5.2, y + 1.3, 4)
    const offsetY = this.fbm(x - 3.7, y + 9.2, 4)
    return [x + offsetX * strength, y + offsetY * strength]
  }
}

export function fade(value: number): number {
  return value * value * value * (value * (value * 6 - 15) + 10)
}

export function lerp(a: number, b: number, t: number): number {
  return a + (b - a) * t
}

export function clamp(value: number, min: number, max: number): number {
  return value < min ? min : value > max ? max : value
}

export function smoothstep(edge0: number, edge1: number, value: number): number {
  if (edge0 === edge1) return value < edge0 ? 0 : 1
  const t = clamp((value - edge0) / (edge1 - edge0), 0, 1)
  return t * t * (3 - 2 * t)
}


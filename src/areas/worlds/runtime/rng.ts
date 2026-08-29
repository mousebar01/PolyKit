/** Small deterministic random primitives used by every local world stage. */

export type Rng = () => number

export function hashString(value: string): number {
  let hash = 2166136261 >>> 0
  for (let index = 0; index < value.length; index += 1) {
    hash ^= value.charCodeAt(index)
    hash = Math.imul(hash, 16777619)
  }
  return hash >>> 0
}

/** mulberry32: fast, reproducible PRNG returning values in [0, 1). */
export function mulberry32(seed: number): Rng {
  let state = seed >>> 0
  return () => {
    state |= 0
    state = (state + 0x6d2b79f5) | 0
    let value = Math.imul(state ^ (state >>> 15), 1 | state)
    value = (value + Math.imul(value ^ (value >>> 7), 61 | value)) ^ value
    return ((value ^ (value >>> 14)) >>> 0) / 4294967296
  }
}

/** Independent, stable stream for a named world subsystem. */
export function subRng(seed: number, label: string): Rng {
  return mulberry32((seed ^ hashString(label)) >>> 0)
}

export function range(rng: Rng, min: number, max: number): number {
  return min + rng() * (max - min)
}

/** Box-Muller normal distribution, used by cluster placement. */
export function gaussian(rng: Rng, mean = 0, standardDeviation = 1): number {
  const u = Math.max(rng(), 1e-9)
  const v = rng()
  return mean + standardDeviation * Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v)
}

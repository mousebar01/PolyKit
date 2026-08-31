import type { ScenePlan } from './scenePlan'
import type { Instance, WorldSpec } from './types'

export const WORLD_RUNTIME_VERSION = 1 as const

export const WORLD_RUNTIME_GATE_IDS = ['construction', 'visual', 'gameplay'] as const
export type WorldRuntimeGateId = (typeof WORLD_RUNTIME_GATE_IDS)[number]
export type WorldRuntimeGateStatus = 'pending' | 'pass' | 'needs_review' | 'fail'

export interface WorldRuntimeIssue {
  id: string
  code: string
  gate: WorldRuntimeGateId
  severity: 'info' | 'warning' | 'error'
  message: string
  subjectId?: string
  measured?: number
  expected?: number | string
}

export interface WorldRuntimeGateState {
  status: WorldRuntimeGateStatus
  issues: WorldRuntimeIssue[]
  checked_at?: string
}

/**
 * Derived facts about the current world. Workflow progress deliberately does
 * not live here; WorkflowRun owns execution state and external callers decide
 * what to do next.
 */
export interface WorldRuntimeQuality {
  construction: WorldRuntimeGateState
  visual: WorldRuntimeGateState
  gameplay: WorldRuntimeGateState
  updated_at?: string
}

export interface BuildAnchorSpec {
  id: string
  partId?: string
  position?: [number, number, number]
  normal?: [number, number, number]
}

export interface BuildAttachmentSpec {
  id: string
  from: string
  to: string
  mode: 'flush' | 'support' | 'inside' | 'passes-through'
  tolerance: number
}

export interface BuildingSpec {
  id: string
  name: string
  generator: 'blender-parametric'
  parameters: Record<string, string | number | boolean>
  anchors: BuildAnchorSpec[]
  attachments: BuildAttachmentSpec[]
}

/**
 * Authoring/build contract. Environment generation and construction rules live
 * here; semantic scene relationships remain in ScenePlan and gameplay stays in
 * GameSpec.
 */
export interface BuildSpec {
  kind: 'polykit.build-spec'
  version: 1
  environment: WorldSpec | null
  buildings: BuildingSpec[]
}

export interface GamePlayerSpec {
  controller: 'walk'
  radius: number
  height: number
  move_speed: number
  spawn:
    | { mode: 'auto' }
    | { mode: 'fixed'; position: [number, number, number]; yaw?: number }
}

export interface GameInteractionSpec {
  id: string
  objectId: string
  action: string
  socket?: string
}

export interface GameObjectiveSpec {
  id: string
  label: string
  trigger: string
  targetId?: string
}

export interface GameSpec {
  kind: 'polykit.game-spec'
  version: 1
  player: GamePlayerSpec
  collision: {
    mode: 'semantic-aabb' | 'manifest'
  }
  interactions: GameInteractionSpec[]
  objectives: GameObjectiveSpec[]
}

export interface WorldRuntime {
  version: typeof WORLD_RUNTIME_VERSION
  intent: {
    prompt: string
  }
  /** Authoring/build inputs for outdoor environment and constructed structures. */
  build: BuildSpec
  /** Semantic object graph and solved transforms, independent of the renderer. */
  scene: ScenePlan | null
  /** Deterministic build output owned by the runtime rather than the renderer. */
  compiled: {
    instances: Instance[]
  }
  /** Runtime/gameplay contract consumed by the browser game layer. */
  game: GameSpec
  /** Derived world facts; never workflow/task progress. */
  quality: WorldRuntimeQuality
}

export function createDefaultBuildSpec(): BuildSpec {
  return {
    kind: 'polykit.build-spec',
    version: 1,
    environment: null,
    buildings: [],
  }
}

export function createDefaultGameSpec(): GameSpec {
  return {
    kind: 'polykit.game-spec',
    version: 1,
    player: {
      controller: 'walk',
      radius: 0.28,
      height: 1.7,
      move_speed: 2.8,
      spawn: { mode: 'auto' },
    },
    collision: { mode: 'semantic-aabb' },
    interactions: [],
    objectives: [],
  }
}

function pendingGate(): WorldRuntimeGateState {
  return { status: 'pending', issues: [] }
}

export function createInitialRuntime(prompt = ''): WorldRuntime {
  return {
    version: WORLD_RUNTIME_VERSION,
    intent: { prompt },
    build: createDefaultBuildSpec(),
    scene: null,
    compiled: { instances: [] },
    game: createDefaultGameSpec(),
    quality: {
      construction: pendingGate(),
      visual: pendingGate(),
      gameplay: pendingGate(),
    },
  }
}

import type { ScenePlan } from './scenePlan'
import type { Instance, WorldSpec } from './types'

export const WORLD_RUNTIME_VERSION = 1 as const

export const WORLD_RUNTIME_STAGE_IDS = [
  'intent',
  'blockout',
  'structure',
  'environment',
  'assets',
  'materials',
  'lighting',
  'gameplay',
  'optimization',
] as const

export type WorldRuntimeStageId = (typeof WORLD_RUNTIME_STAGE_IDS)[number]
export type WorldRuntimeStageStatus = 'locked' | 'ready' | 'running' | 'passed' | 'failed'

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

export interface WorldRuntimeStage {
  id: WorldRuntimeStageId
  status: WorldRuntimeStageStatus
  note?: string
  updated_at?: string
}

export interface WorldRuntimeState {
  stages: WorldRuntimeStage[]
  gates: Record<WorldRuntimeGateId, WorldRuntimeGateState>
  updated_at?: string
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
  /** Procedural outdoor/environment build specification. */
  build: WorldSpec | null
  /** Semantic object graph and solved transforms, independent of the renderer. */
  scene: ScenePlan | null
  /** Deterministic build output owned by the runtime rather than the renderer. */
  compiled: {
    instances: Instance[]
  }
  /** Runtime/gameplay contract consumed by the browser game layer. */
  game: GameSpec
  /** Resumable Agent pass state and explicit quality gates. */
  state: WorldRuntimeState
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
    build: null,
    scene: null,
    compiled: { instances: [] },
    game: createDefaultGameSpec(),
    state: {
      stages: WORLD_RUNTIME_STAGE_IDS.map((id, index) => ({
        id,
        status: index === 0 ? 'ready' : 'locked',
      })),
      gates: {
        construction: pendingGate(),
        visual: pendingGate(),
        gameplay: pendingGate(),
      },
    },
  }
}

export function currentRuntimeStage(state: WorldRuntimeState): WorldRuntimeStage | null {
  if (state.stages.length === 0) return null
  return state.stages.find((stage) => stage.status === 'running')
    ?? state.stages.find((stage) => stage.status === 'failed')
    ?? state.stages.find((stage) => stage.status === 'ready')
    ?? state.stages.find((stage) => stage.status === 'locked')
    ?? state.stages[state.stages.length - 1]
}

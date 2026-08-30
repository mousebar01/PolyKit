/** Renderer-neutral scene-plan vocabulary produced by the server planner. */

export type SceneObjectRole = 'room' | 'background' | 'context' | 'hero' | 'manipulated' | 'distractor' | string

export interface ScenePlanAssetRef {
  workspacePath?: string
  assetId?: string
  runId?: string
  source?: string
  [key: string]: unknown
}

export interface ScenePlanObject {
  id: string
  name: string
  role: SceneObjectRole
  category?: string | null
  description?: string
  aliases?: string[]
  size: [number, number, number]
  asset?: ScenePlanAssetRef | null
  constraints?: Record<string, unknown>
}

export interface ScenePlanInstance {
  id: string
  objectId: string
  position: [number, number, number]
  rotation: [number, number, number]
  scale: number
  roomId?: string | null
}

export interface ScenePlanBounds {
  width: number
  depth: number
  height: number
}

export interface ScenePlanDiagnostic {
  code?: string
  severity?: 'info' | 'warning' | 'error' | string
  object_id?: string
  message?: string
  [key: string]: unknown
}

export interface ScenePlan {
  schemaVersion?: number
  kind: 'polykit.scene-plan'
  sceneId?: string
  sceneKind?: 'indoor' | 'outdoor' | 'mixed' | string
  prompt?: string
  seed?: number
  bounds: ScenePlanBounds
  objects: ScenePlanObject[]
  relations?: Array<{ subject: string; type: string; object: string }>
  instances: ScenePlanInstance[]
  diagnostics?: ScenePlanDiagnostic[]
  metadata?: Record<string, unknown>
}

function isFinitePositive(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value) && value > 0
}

function isVector3(value: unknown): value is [number, number, number] {
  return Array.isArray(value) && value.length === 3 && value.every((item) => typeof item === 'number' && Number.isFinite(item))
}

/** Keep the canvas out of the loading/empty state for partial plans. */
export function isRenderableScenePlan(value: unknown): value is ScenePlan {
  if (!value || typeof value !== 'object') return false
  const plan = value as Partial<ScenePlan>
  if (plan.kind !== 'polykit.scene-plan' || !Array.isArray(plan.objects) || !Array.isArray(plan.instances)) return false
  if (!plan.bounds || !isFinitePositive(plan.bounds.width) || !isFinitePositive(plan.bounds.depth) || !isFinitePositive(plan.bounds.height)) return false
  const objectIds = new Set<string>()
  for (const object of plan.objects) {
    if (!object || typeof object !== 'object' || typeof object.id !== 'string' || typeof object.name !== 'string' || !isVector3(object.size)) return false
    objectIds.add(object.id)
  }
  return plan.instances.length > 0 && plan.instances.every((instance) => (
    Boolean(instance)
    && typeof instance.id === 'string'
    && typeof instance.objectId === 'string'
    && objectIds.has(instance.objectId)
    && isVector3(instance.position)
    && isVector3(instance.rotation)
    && typeof instance.scale === 'number'
    && Number.isFinite(instance.scale)
    && instance.scale > 0
  ))
}


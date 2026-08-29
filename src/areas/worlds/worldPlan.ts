/** Paper-derived planning vocabulary shared by the Agent and the world UI. */
export const WORLDCLAW_STAGE_IDS = [
  'intent',
  'plan',
  'terrain',
  'placement',
  'assets',
  'materials',
  'refine',
] as const

export type WorldClawStageId = (typeof WORLDCLAW_STAGE_IDS)[number]
export type WorldClawStageStatus = 'pending' | 'running' | 'done' | 'blocked'

export interface WorldAgentStage {
  id: WorldClawStageId
  status: WorldClawStageStatus
  note?: string
  updated_at?: string
}

export interface WorldAgentPlan {
  version: 1
  source: 'worldclaw-paper'
  prompt?: string
  stages: WorldAgentStage[]
  updated_at?: string
}

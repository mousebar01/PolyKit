import { lazy } from 'react'
import type { Page } from '@shared/stores/navStore'

const AssetsPage     = lazy(() => import('@areas/assets/AssetsPage'))
const WorldsPage     = lazy(() => import('@areas/worlds/WorldsPage'))
const WorkflowsPage  = lazy(() => import('@areas/workflows/WorkflowsPage'))
const NodePacksPage  = lazy(() => import('@areas/node-packs/NodePacksPage'))
const AgentPage      = lazy(() => import('@areas/agent/AgentPage'))
const SettingsPage   = lazy(() => import('@areas/settings/SettingsPage'))

export interface RouteConfig {
  component:    React.ComponentType
  wrapperClass: string
}

export const ROUTES: Record<Page, RouteConfig> = {
  assets:    { component: AssetsPage,    wrapperClass: 'flex min-h-0 flex-1 overflow-hidden' },
  worlds:    { component: WorldsPage,    wrapperClass: 'flex min-h-0 flex-1 overflow-hidden' },
  workflows: { component: WorkflowsPage, wrapperClass: 'flex min-h-0 flex-1 overflow-hidden' },
  nodePacks: { component: NodePacksPage,  wrapperClass: 'min-h-0 flex-1 overflow-y-auto' },
  agent:     { component: AgentPage,      wrapperClass: 'min-h-0 flex-1 overflow-hidden' },
  settings:  { component: SettingsPage,  wrapperClass: 'min-h-0 flex-1 overflow-hidden' },
}

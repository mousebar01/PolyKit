import { lazy } from 'react'
import type { Page } from '@shared/stores/navStore'

const AssetsPage     = lazy(() => import('@areas/assets/AssetsPage'))
const WorkflowsPage  = lazy(() => import('@areas/workflows/WorkflowsPage'))
const NodePacksPage  = lazy(() => import('@areas/node-packs/NodePacksPage'))
const SettingsPage   = lazy(() => import('@areas/settings/SettingsPage'))

export interface RouteConfig {
  component:    React.ComponentType
  wrapperClass: string
}

export const ROUTES: Record<Page, RouteConfig> = {
  assets:    { component: AssetsPage,    wrapperClass: 'flex flex-1 overflow-hidden' },
  workflows: { component: WorkflowsPage, wrapperClass: 'flex flex-1 overflow-hidden' },
  nodePacks: { component: NodePacksPage,  wrapperClass: 'flex-1 overflow-y-auto'      },
  settings:  { component: SettingsPage,  wrapperClass: 'flex-1 overflow-hidden'      },
}

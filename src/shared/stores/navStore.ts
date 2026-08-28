import { create } from 'zustand'

export type Page = 'assets' | 'worlds' | 'workflows' | 'nodePacks' | 'agent' | 'settings'
export type SettingsSection = 'application' | 'agent' | 'storage' | 'integrations' | 'network' | 'about'

interface NavState {
  currentPage: Page
  pendingWorkflowNodePackId: string | null
  pendingSettingsSection: SettingsSection | null
  navigate: (page: Page) => void
  openNodePackInWorkflow: (nodePackId: string) => void
  consumeWorkflowNodePack: () => string | null
  openSettings: (section?: SettingsSection) => void
  consumeSettingsSection: () => SettingsSection | null
}

export const useNavStore = create<NavState>((set, get) => ({
  currentPage: 'assets',
  pendingWorkflowNodePackId: null,
  pendingSettingsSection: null,
  navigate: (page) => set({ currentPage: page, pendingSettingsSection: null }),
  openNodePackInWorkflow: (nodePackId) => set({
    currentPage: 'workflows',
    pendingWorkflowNodePackId: nodePackId,
    pendingSettingsSection: null,
  }),
  consumeWorkflowNodePack: () => {
    const nodePackId = get().pendingWorkflowNodePackId
    if (nodePackId) set({ pendingWorkflowNodePackId: null })
    return nodePackId
  },
  openSettings: (section = 'application') => set({
    currentPage: 'settings',
    pendingSettingsSection: section,
  }),
  consumeSettingsSection: () => {
    const section = get().pendingSettingsSection
    if (section) set({ pendingSettingsSection: null })
    return section
  },
}))

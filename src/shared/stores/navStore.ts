import { create } from 'zustand'

export type Page = 'assets' | 'workflows' | 'nodePacks' | 'settings'

interface NavState {
  currentPage: Page
  pendingWorkflowNodePackId: string | null
  navigate: (page: Page) => void
  openNodePackInWorkflow: (nodePackId: string) => void
  consumeWorkflowNodePack: () => string | null
}

export const useNavStore = create<NavState>((set, get) => ({
  currentPage: 'assets',
  pendingWorkflowNodePackId: null,
  navigate: (page) => set({ currentPage: page }),
  openNodePackInWorkflow: (nodePackId) => set({
    currentPage: 'workflows',
    pendingWorkflowNodePackId: nodePackId,
  }),
  consumeWorkflowNodePack: () => {
    const nodePackId = get().pendingWorkflowNodePackId
    if (nodePackId) set({ pendingWorkflowNodePackId: null })
    return nodePackId
  },
}))

import { create } from 'zustand'
import axios from 'axios'

import { useAppStore } from '@shared/stores/appStore'
import type { Workflow } from '@shared/types/runtime.d'
import type { WorkflowNodePack } from './mockNodePacks'
import { compileServerWorkflow } from './executionPayload'

export interface WorkflowRunState {
  status: 'idle' | 'running' | 'done' | 'error'
  blockIndex: number
  blockTotal: number
  blockProgress: number
  blockStep: string
  outputUrl?: string
  outputPath?: string
  error?: string
}

const IDLE: WorkflowRunState = {
  status: 'idle',
  blockIndex: 0,
  blockTotal: 0,
  blockProgress: 0,
  blockStep: '',
}

const _cancel = { current: false }
const _activeRunId = { current: null as string | null }

interface WorkflowRunStore {
  runState: WorkflowRunState
  activeNodeId: string | null
  activeWorkflowId: string | null
  nodeImageOutputs: Record<string, string>

  run: (workflow: Workflow, allNodePacks: WorkflowNodePack[], overrideImageData?: string) => Promise<void>
  cancel: () => void
  reset: () => void
}

export const useWorkflowRunStore = create<WorkflowRunStore>((set) => ({
  runState: IDLE,
  activeNodeId: null,
  activeWorkflowId: null,
  nodeImageOutputs: {},

  async run(workflow, allNodePacks, overrideImageData?) {
    _cancel.current = false
    _activeRunId.current = null

    const appState = useAppStore.getState()
    const apiUrl = appState.apiUrl
    const selectedImagePath = appState.selectedImagePath ?? undefined
    const selectedImageData = overrideImageData ?? appState.selectedImageData ?? undefined

    set({
      activeNodeId: null,
      activeWorkflowId: workflow.id,
      nodeImageOutputs: {},
      runState: {
        status: 'running',
        blockIndex: 0,
        blockTotal: 1,
        blockProgress: 0,
        blockStep: 'Compiling workflow…',
      },
    })

    appState.setCurrentJob({
      id: crypto.randomUUID(),
      imageFile: '__workflow__',
      status: 'generating',
      progress: 0,
      createdAt: Date.now(),
    })

    try {
      const compiled = await compileServerWorkflow(workflow, allNodePacks, {
        selectedImagePath,
        selectedImageData,
      })
      if (!compiled.ok) {
        throw new Error(compiled.error)
      }
      if (_cancel.current) return

      const client = axios.create({ baseURL: apiUrl })
      set((state) => ({
        runState: {
          ...state.runState,
          blockProgress: 5,
          blockStep: 'Submitting workflow…',
        },
      }))

      const { data } = await client.post<{ run_id: string }>('/workflow-runs/execute', compiled.payload)
      _activeRunId.current = data.run_id

      while (!_cancel.current) {
        await new Promise((resolve) => setTimeout(resolve, 1200))
        if (_cancel.current || !_activeRunId.current) return

        const { data: status } = await client.get<{
          status: string
          progress?: number
          step?: string
          output_url?: string
          error?: string
        }>(`/workflow-runs/${_activeRunId.current}`)

        if (status.status === 'done') {
          const outputUrl = status.output_url
          _activeRunId.current = null
          set({
            activeNodeId: null,
            runState: {
              status: 'done',
              blockIndex: 1,
              blockTotal: 1,
              blockProgress: 100,
              blockStep: 'Workflow complete',
              outputUrl,
            },
          })
          if (outputUrl) useAppStore.getState().pushMeshUrl(outputUrl)
          useAppStore.getState().updateCurrentJob({ status: 'done', progress: 100, outputUrl })
          return
        }

        if (status.status === 'cancelled') {
          _activeRunId.current = null
          set({ runState: IDLE, activeNodeId: null, activeWorkflowId: null })
          useAppStore.getState().setCurrentJob(null)
          return
        }

        if (status.status === 'error' || status.status === 'interrupted') {
          throw new Error(status.error ?? 'Workflow execution failed')
        }

        set((state) => ({
          runState: {
            ...state.runState,
            blockProgress: status.progress ?? state.runState.blockProgress,
            blockStep: status.step ?? 'Executing workflow…',
          },
        }))
        useAppStore.getState().updateCurrentJob({
          status: 'generating',
          progress: status.progress,
          step: status.step,
        })
      }
    } catch (error) {
      if (_cancel.current) return
      _activeRunId.current = null
      const message = error instanceof Error ? error.message : String(error)
      set((state) => ({
        activeNodeId: null,
        runState: { ...state.runState, status: 'error', error: message },
      }))
      useAppStore.getState().updateCurrentJob({ status: 'error', error: message })
    }
  },

  cancel() {
    _cancel.current = true
    const runId = _activeRunId.current
    _activeRunId.current = null
    if (runId) {
      const client = axios.create({ baseURL: useAppStore.getState().apiUrl })
      void client.post(`/workflow-runs/${runId}/cancel`).catch(() => {})
    }
    set({ runState: IDLE, activeNodeId: null, activeWorkflowId: null, nodeImageOutputs: {} })
    useAppStore.getState().setCurrentJob(null)
  },

  reset() {
    _cancel.current = false
    _activeRunId.current = null
    set({ runState: IDLE, activeNodeId: null, activeWorkflowId: null, nodeImageOutputs: {} })
  },
}))

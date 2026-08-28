import { create } from 'zustand'

import { useAppStore } from '@shared/stores/appStore'
import type { Workflow } from '@shared/types/runtime.d'
import type { WorkflowNodePack } from './mockNodePacks'
import { compileServerWorkflow } from './executionPayload'
import { createWorkflowRunsClient } from '@shared/services/workflowRuns'

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
const _pollAbortController = { current: null as AbortController | null }

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
    _pollAbortController.current?.abort()
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

      const client = createWorkflowRunsClient(apiUrl)
      // Canonical server-owned execution endpoint: POST /workflow-runs/execute.
      set((state) => ({
        runState: {
          ...state.runState,
          blockProgress: 5,
          blockStep: 'Submitting workflow…',
        },
      }))

      const data = await client.submit(compiled.payload)
      _activeRunId.current = data.run_id
      const abortController = new AbortController()
      _pollAbortController.current = abortController

      const status = await client.poll(data.run_id, {
        signal: abortController.signal,
        onUpdate: (nextStatus) => {
          if (_cancel.current || isTerminal(nextStatus.status)) return
          set((state) => ({
            runState: {
              ...state.runState,
              blockProgress: nextStatus.progress ?? state.runState.blockProgress,
              blockStep: nextStatus.step ?? 'Executing workflow…',
            },
          }))
          useAppStore.getState().updateCurrentJob({
            status: 'generating',
            progress: nextStatus.progress,
            step: nextStatus.step,
          })
        },
      })

      if (_cancel.current) return
      _activeRunId.current = null
      _pollAbortController.current = null

      if (status.status === 'done') {
        const outputUrl = status.output_url
        const artifactKind = status.meta?.artifact_kind
        const imagePreviewSources = artifactKind === 'image'
          ? workflow.nodes
            .filter((node) => node.type === 'previewNode')
            .map((node) => workflow.edges.find((edge) => edge.target === node.id)?.source)
            .filter((source): source is string => Boolean(source))
          : []
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
          nodeImageOutputs: outputUrl
            ? Object.fromEntries(imagePreviewSources.map((source) => [source, outputUrl]))
            : {},
        })
        if (outputUrl && artifactKind !== 'image') useAppStore.getState().pushMeshUrl(outputUrl)
        useAppStore.getState().updateCurrentJob({
          status: 'done',
          progress: 100,
          outputUrl,
          outputKind: artifactKind === 'image' ? 'image' : 'mesh',
        })
        return
      }

      if (status.status === 'cancelled') {
        set({ runState: IDLE, activeNodeId: null, activeWorkflowId: null })
        useAppStore.getState().setCurrentJob(null)
        return
      }

      if (status.status === 'error' || status.status === 'interrupted') {
        throw new Error(status.error ?? 'Workflow execution failed')
      }

      throw new Error(`Workflow returned an unknown terminal status: ${status.status}`)
    } catch (error) {
      if (_cancel.current) return
      _activeRunId.current = null
      _pollAbortController.current = null
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
    _pollAbortController.current?.abort()
    _pollAbortController.current = null
    const runId = _activeRunId.current
    _activeRunId.current = null
    if (runId) {
      const client = createWorkflowRunsClient(useAppStore.getState().apiUrl)
      void client.cancel(runId).catch(() => {})
    }
    set({ runState: IDLE, activeNodeId: null, activeWorkflowId: null, nodeImageOutputs: {} })
    useAppStore.getState().setCurrentJob(null)
  },

  reset() {
    _cancel.current = false
    _pollAbortController.current?.abort()
    _pollAbortController.current = null
    _activeRunId.current = null
    set({ runState: IDLE, activeNodeId: null, activeWorkflowId: null, nodeImageOutputs: {} })
  },
}))

function isTerminal(status: string): boolean {
  return status === 'done' || status === 'error' || status === 'cancelled' || status === 'interrupted'
}

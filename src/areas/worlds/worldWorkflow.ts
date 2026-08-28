import type { Workflow } from '@shared/types/runtime.d'
import type { WorkflowRunStatus } from '@shared/services/workflowRuns'
import { createWorkflowRunsClient } from '@shared/services/workflowRuns'
import { useAppStore } from '@shared/stores/appStore'
import type { WorkflowNodePack } from '@areas/workflows/mockNodePacks'
import { compileServerWorkflow } from '@areas/workflows/executionPayload'

export interface WorldHeroRunOptions {
  workflow: Workflow
  nodePacks: WorkflowNodePack[]
  imageNodeId: string
  conceptImagePath: string
  onUpdate?: (status: WorkflowRunStatus) => void | Promise<void>
  signal?: AbortSignal
}

/**
 * Bind one workspace concept image to an existing editable workflow.
 *
 * This is deliberately a small adapter: the workflow remains user-owned and
 * the FastAPI run coordinator remains the only execution path. Worlds never
 * call a cloud provider directly.
 */
export async function runWorldHero(options: WorldHeroRunOptions): Promise<WorkflowRunStatus> {
  const compiled = await compileServerWorkflow(options.workflow, options.nodePacks, {
    imageNodeWorkspacePaths: { [options.imageNodeId]: options.conceptImagePath },
  })
  if (!compiled.ok) throw new Error(compiled.error)

  const client = createWorkflowRunsClient(useAppStore.getState().apiUrl)
  const submission = await client.submit(compiled.payload, { signal: options.signal })
  return client.poll(submission.run_id, {
    signal: options.signal,
    onUpdate: options.onUpdate,
  })
}

export function workspacePathFromOutput(outputUrl: string | undefined): string | undefined {
  if (!outputUrl) return undefined
  return outputUrl.replace(/^\/workspace\//, '')
}


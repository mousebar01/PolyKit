import axios, { type AxiosInstance, type AxiosRequestConfig } from 'axios'

/** Status values emitted by the server-side workflow run coordinator. */
export type WorkflowRunStatusValue =
  | 'pending'
  | 'running'
  | 'done'
  | 'error'
  | 'cancelled'
  | 'interrupted'

/** The wire representation returned by `/workflow-runs/*`. */
export interface WorkflowRunStatus {
  run_id: string
  status: WorkflowRunStatusValue | string
  progress?: number
  step?: string
  output_url?: string
  error?: string
  scene_candidate?: Record<string, unknown>
  meta?: Record<string, unknown>
}

export interface WorkflowRunSubmission {
  run_id: string
  status?: WorkflowRunStatusValue | string
  workflow_id?: string
  queued_nodes?: number
}

export interface WorkflowRunPollOptions {
  /** Delay between status requests. Defaults to 1200ms, matching the UI poller. */
  intervalMs?: number
  signal?: AbortSignal
  onUpdate?: (status: WorkflowRunStatus) => void | Promise<void>
}

export interface WorkflowRunRequestOptions {
  signal?: AbortSignal
}

export interface WorkflowRunsClient {
  /** Submit a compiled workflow DAG to the server. */
  submit: (payload: unknown, options?: WorkflowRunRequestOptions) => Promise<WorkflowRunSubmission>
  /** Alias for callers that use the server endpoint's action name. */
  execute: (payload: unknown, options?: WorkflowRunRequestOptions) => Promise<WorkflowRunSubmission>
  /** Fetch one status snapshot. */
  status: (runId: string, options?: WorkflowRunRequestOptions) => Promise<WorkflowRunStatus>
  /** Poll until the server reports a terminal status. */
  poll: (runId: string, options?: WorkflowRunPollOptions) => Promise<WorkflowRunStatus>
  /** Ask the server to cancel a run. */
  cancel: (runId: string, options?: WorkflowRunRequestOptions) => Promise<void>
}

export const WORKFLOW_RUN_TERMINAL_STATUSES: ReadonlySet<string> = new Set([
  'done',
  'error',
  'cancelled',
  'interrupted',
])

export function isWorkflowRunTerminal(status: string): boolean {
  return WORKFLOW_RUN_TERMINAL_STATUSES.has(status)
}

function requestConfig(options?: WorkflowRunRequestOptions): AxiosRequestConfig | undefined {
  return options?.signal ? { signal: options.signal } : undefined
}

function assertRunId(runId: string): void {
  if (!runId.trim()) throw new Error('A workflow run id is required')
}

function wait(ms: number, signal?: AbortSignal): Promise<void> {
  if (signal?.aborted) return Promise.reject(signal.reason ?? new DOMException('Aborted', 'AbortError'))
  return new Promise((resolve, reject) => {
    const timer = globalThis.setTimeout(resolve, ms)
    const abort = (): void => {
      globalThis.clearTimeout(timer)
      reject(signal?.reason ?? new DOMException('Aborted', 'AbortError'))
    }
    signal?.addEventListener('abort', abort, { once: true })
  })
}

/**
 * Browser-safe client for the server-owned workflow run lifecycle.
 *
 * The client intentionally leaves the response in server wire format. This
 * keeps it useful to both the workflow editor and reconnecting views, while
 * existing adapters can continue mapping `output_url` to `outputUrl` locally.
 */
export function createWorkflowRunsClient(apiUrl = '', instance?: AxiosInstance): WorkflowRunsClient {
  const client = instance ?? axios.create({ baseURL: apiUrl.replace(/\/+$/, '') })

  const submit = async (
    payload: unknown,
    options?: WorkflowRunRequestOptions,
  ): Promise<WorkflowRunSubmission> => {
    const { data } = await client.post<WorkflowRunSubmission>('/workflow-runs/execute', payload, requestConfig(options))
    return data
  }

  const status = async (runId: string, options?: WorkflowRunRequestOptions): Promise<WorkflowRunStatus> => {
    assertRunId(runId)
    const { data } = await client.get<WorkflowRunStatus>(
      `/workflow-runs/${encodeURIComponent(runId)}`,
      requestConfig(options),
    )
    return data
  }

  const poll = async (runId: string, options: WorkflowRunPollOptions = {}): Promise<WorkflowRunStatus> => {
    assertRunId(runId)
    const intervalMs = Math.max(0, options.intervalMs ?? 1200)
    let current = await status(runId, options)
    await options.onUpdate?.(current)

    while (!isWorkflowRunTerminal(current.status)) {
      await wait(intervalMs, options.signal)
      current = await status(runId, options)
      await options.onUpdate?.(current)
    }
    return current
  }

  const cancel = async (runId: string, options?: WorkflowRunRequestOptions): Promise<void> => {
    assertRunId(runId)
    await client.post(`/workflow-runs/${encodeURIComponent(runId)}/cancel`, undefined, requestConfig(options))
  }

  return { submit, execute: submit, status, poll, cancel }
}

/** Standalone aliases make the common one-shot operations easy to test/use. */
export async function submitWorkflowRun(
  apiUrl: string,
  payload: unknown,
  options?: WorkflowRunRequestOptions,
): Promise<WorkflowRunSubmission> {
  return createWorkflowRunsClient(apiUrl).submit(payload, options)
}

export async function pollWorkflowRun(
  apiUrl: string,
  runId: string,
  options?: WorkflowRunPollOptions,
): Promise<WorkflowRunStatus> {
  return createWorkflowRunsClient(apiUrl).poll(runId, options)
}

export async function cancelWorkflowRun(
  apiUrl: string,
  runId: string,
  options?: WorkflowRunRequestOptions,
): Promise<void> {
  return createWorkflowRunsClient(apiUrl).cancel(runId, options)
}

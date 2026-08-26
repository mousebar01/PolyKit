import type { Workflow } from '@shared/types/runtime.d'


const LEGACY_WORKFLOWS_KEY = 'polykit-web-workflows'
const MIGRATION_MARKER_KEY = 'polykit-workflows-server-migrated-v1'

const API_URL = (
  import.meta.env.VITE_POLYKIT_API_URL?.trim() || window.location.origin
).replace(/\/+$/, '')

async function request(path: string, init?: RequestInit): Promise<Response> {
  return fetch(`${API_URL}${path}`, init)
}

async function listServerWorkflows(): Promise<Workflow[]> {
  const response = await request('/workflow-definitions')
  if (!response.ok) throw new Error(`Could not list workflows: HTTP ${response.status}`)
  const value = await response.json()
  return Array.isArray(value) ? value as Workflow[] : []
}

async function saveServerWorkflow(workflow: Workflow): Promise<void> {
  const response = await request(`/workflow-definitions/${encodeURIComponent(workflow.id)}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(workflow),
  })
  if (!response.ok) throw new Error(`Could not save workflow: ${await response.text()}`)
}

function readLegacyWorkflows(): Workflow[] {
  try {
    const value = JSON.parse(localStorage.getItem(LEGACY_WORKFLOWS_KEY) ?? '[]')
    return Array.isArray(value) ? value as Workflow[] : []
  } catch {
    return []
  }
}

async function migrateLegacyWorkflows(serverWorkflows: Workflow[]): Promise<Workflow[]> {
  if (localStorage.getItem(MIGRATION_MARKER_KEY) === '1') return serverWorkflows

  const legacy = readLegacyWorkflows()
  if (legacy.length === 0) {
    localStorage.removeItem(LEGACY_WORKFLOWS_KEY)
    localStorage.setItem(MIGRATION_MARKER_KEY, '1')
    return serverWorkflows
  }

  const serverById = new Map(serverWorkflows.map((workflow) => [workflow.id, workflow]))
  let changed = false
  for (const workflow of legacy) {
    const current = serverById.get(workflow.id)
    if (current && String(current.updatedAt ?? '') >= String(workflow.updatedAt ?? '')) continue
    await saveServerWorkflow(workflow)
    serverById.set(workflow.id, workflow)
    changed = true
  }

  localStorage.removeItem(LEGACY_WORKFLOWS_KEY)
  localStorage.setItem(MIGRATION_MARKER_KEY, '1')
  return changed ? listServerWorkflows() : serverWorkflows
}

function parseWorkflowFile(value: unknown): Workflow {
  if (!value || typeof value !== 'object') {
    throw new Error('The selected file is not a valid PolyKit workflow.')
  }
  const record = value as Record<string, unknown>
  const hasGraph = Array.isArray(record.nodes) && Array.isArray(record.edges)
  const hasLegacyBlocks = Array.isArray(record.blocks)
  if (
    typeof record.id !== 'string' || !record.id.trim() ||
    typeof record.name !== 'string' ||
    (!hasGraph && !hasLegacyBlocks)
  ) {
    throw new Error('The selected file is not a valid PolyKit workflow.')
  }
  return value as Workflow
}

function chooseWorkflowFile(): Promise<Workflow | null> {
  return new Promise((resolve, reject) => {
    const input = document.createElement('input')
    input.type = 'file'
    input.accept = '.json,application/json'
    input.onchange = async () => {
      const file = input.files?.[0]
      if (!file) {
        resolve(null)
        return
      }
      try {
        resolve(parseWorkflowFile(JSON.parse(await file.text())))
      } catch (error) {
        reject(error)
      }
    }
    input.click()
  })
}

export function installServerWorkflowBridge(): void {
  const existingWorkflows = window.polykit.workflows
  window.polykit.workflows = {
    ...existingWorkflows,
    list: async () => migrateLegacyWorkflows(await listServerWorkflows()),
    save: async (workflow: Workflow) => {
      try {
        await saveServerWorkflow(workflow)
        return { success: true }
      } catch (error) {
        return { success: false, error: error instanceof Error ? error.message : String(error) }
      }
    },
    delete: async (id: string) => {
      try {
        const response = await request(`/workflow-definitions/${encodeURIComponent(id)}`, { method: 'DELETE' })
        if (!response.ok) throw new Error(await response.text())
        return { success: true }
      } catch (error) {
        return { success: false, error: error instanceof Error ? error.message : String(error) }
      }
    },
    import: async () => {
      try {
        const workflow = await chooseWorkflowFile()
        if (!workflow) return { success: false }
        await saveServerWorkflow(workflow)
        return { success: true, workflow }
      } catch (error) {
        return { success: false, error: error instanceof Error ? error.message : String(error) }
      }
    },
  }

  const existingSettings = window.polykit.settings
  window.polykit.settings = {
    ...existingSettings,
    get: async () => {
      const settings = await existingSettings.get()
      try {
        const response = await request('/settings/paths')
        if (!response.ok) return settings
        const paths = await response.json() as { workflows_dir?: string }
        return paths.workflows_dir ? { ...settings, workflowsDir: paths.workflows_dir } : settings
      } catch {
        return settings
      }
    },
  }
}

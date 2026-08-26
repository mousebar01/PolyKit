import type { DownloadSourceSettings, ProxySettings, Workflow } from '@shared/types/runtime.d'

// Browser deployments normally share an origin with FastAPI. This override
// supports remote UI / GPU server deployments.
const API_URL = (
  import.meta.env.VITE_POLYKIT_API_URL?.trim() ||
  window.location.origin
).replace(/\/+$/, '')

const FILE_PREFIX = 'web-file://'
const files = new Map<string, File>()
const modelProgressListeners = new Set<(data: Record<string, unknown>) => void>()
const installProgressListeners = new Set<(data: Record<string, unknown>) => void>()

type WebSettings = {
  modelsDir: string
  workspaceDir: string
  workflowsDir: string
  nodePacksDir: string
  hfToken?: string
  proxy?: ProxySettings
  sources?: DownloadSourceSettings
}

const defaultSettings: WebSettings = {
  modelsDir: 'browser://models',
  workspaceDir: 'browser://workspace',
  workflowsDir: 'browser://workflows',
  nodePacksDir: 'browser://node-packs',
  proxy: { enabled: false, url: '', username: '', password: '', bypass: '' },
  sources: { huggingfaceEndpoint: '', pypiIndexUrl: '', pytorchIndexUrl: '' },
}

function settingsKey(): string {
  return 'polykit-web-settings'
}

function getStoredSettings(): WebSettings {
  try {
    return { ...defaultSettings, ...(JSON.parse(localStorage.getItem(settingsKey()) ?? '{}') as Partial<WebSettings>) }
  } catch {
    return defaultSettings
  }
}

function setStoredSettings(patch: Partial<WebSettings>): WebSettings {
  const next = { ...getStoredSettings(), ...patch }
  localStorage.setItem(settingsKey(), JSON.stringify(next))
  return next
}

async function request(path: string, init?: RequestInit): Promise<Response> {
  return fetch(`${API_URL}${path}`, init)
}

async function json<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await request(path, init)
  if (!response.ok) throw new Error(`${response.status}: ${await response.text()}`)
  return response.json() as Promise<T>
}

async function checkApi(): Promise<{ success: true; ready: boolean } | { success: false; error: string }> {
  try {
    const response = await request('/health/ready')
    const body = await response.json().catch(() => ({})) as { status?: string }
    if (!response.ok) {
      return { success: false, error: `PolyKit server returned HTTP ${response.status}.` }
    }
    return { success: true, ready: body.status === 'ready' }
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error)
    return { success: false, error: `Cannot reach PolyKit server at ${API_URL}: ${message}` }
  }
}

function chooseFile(accept: string): Promise<string | null> {
  return new Promise((resolve) => {
    const input = document.createElement('input')
    input.type = 'file'
    input.accept = accept
    input.onchange = () => {
      const file = input.files?.[0]
      if (!file) {
        resolve(null)
        return
      }
      const path = `${FILE_PREFIX}${crypto.randomUUID()}/${encodeURIComponent(file.name)}`
      files.set(path, file)
      resolve(path)
    }
    input.click()
  })
}

async function fileBase64(path: string): Promise<string> {
  const file = files.get(path)
  if (!file) throw new Error(`Browser file is no longer available: ${path}`)
  const bytes = new Uint8Array(await file.arrayBuffer())
  let binary = ''
  const chunkSize = 0x8000
  for (let offset = 0; offset < bytes.length; offset += chunkSize) {
    binary += String.fromCharCode(...bytes.subarray(offset, offset + chunkSize))
  }
  return btoa(binary)
}

function downloadBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  link.click()
  URL.revokeObjectURL(url)
}

async function streamModelDownload(
  repoId: string,
  modelId: string,
  skipPrefixes?: string[],
  includePrefixes?: string[],
): Promise<{ success: boolean; error?: string; paused?: boolean; cancelled?: boolean }> {
  try {
    return await streamModelDownloadRequest(repoId, modelId, skipPrefixes, includePrefixes)
  } catch (error) {
    const message = error instanceof Error ? error.message : typeof error === 'string' ? error : 'The model download failed.'
    return { success: false, error: message }
  }
}

async function streamModelDownloadRequest(
  repoId: string,
  modelId: string,
  skipPrefixes?: string[],
  includePrefixes?: string[],
): Promise<{ success: boolean; error?: string; paused?: boolean; cancelled?: boolean }> {
  const query = new URLSearchParams({ repo_id: repoId, model_id: modelId })
  if (skipPrefixes?.length) query.set('skip_prefixes', JSON.stringify(skipPrefixes))
  if (includePrefixes?.length) query.set('include_prefixes', JSON.stringify(includePrefixes))

  const response = await request(`/model/hf-download?${query.toString()}`)
  if (!response.ok) return { success: false, error: await response.text() }
  const reader = response.body?.getReader()
  if (!reader) return { success: false, error: 'The browser could not read the download stream.' }

  const decoder = new TextDecoder()
  let buffer = ''
  let terminalError: string | undefined
  let terminalPaused = false
  let terminalCancelled = false
  while (true) {
    const { done, value } = await reader.read()
    buffer += decoder.decode(value ?? new Uint8Array(), { stream: !done })
    for (const line of buffer.split('\n').slice(0, -1)) {
      if (!line.startsWith('data: ')) continue
      try {
        const data = JSON.parse(line.slice(6)) as Record<string, unknown>
        for (const listener of modelProgressListeners) listener({ ...data, modelId })
        if (typeof data.error === 'string' && data.error) terminalError = data.error
        if (data.paused === true) terminalPaused = true
        if (data.cancelled === true) terminalCancelled = true
      } catch {
        // Ignore incomplete or non-JSON SSE lines.
      }
    }
    buffer = buffer.includes('\n') ? buffer.slice(buffer.lastIndexOf('\n') + 1) : buffer
    if (done) break
  }
  if (terminalError) return { success: false, error: terminalError }
  if (terminalPaused) return { success: false, paused: true }
  if (terminalCancelled) return { success: false, cancelled: true }
  return { success: true }
}

async function uploadWorkspaceAsset(filePath: string): Promise<string | null> {
  const file = files.get(filePath)
  if (!file) return null
  try {
    const fd = new FormData()
    fd.append('file', file, file.name)
    fd.append('collection', 'Workflows')
    const response = await request('/workspace-library/upload', { method: 'POST', body: fd })
    if (!response.ok) return null
    const body = await response.json() as { workspacePath?: string }
    return body.workspacePath ?? null
  } catch {
    return null
  }
}

export interface ServerMeshEntry {
  workspacePath: string
  name: string
  thumbnail?: string
  previewKind?: string
  capability?: string
}

// Server-side meshes can be selected without routing remote paths through the
// local browser filesystem.
async function listServerMeshes(): Promise<ServerMeshEntry[]> {
  try {
    const body = await json<{
      success?: boolean
      entries?: { workspacePath: string; previewKind?: string; capability?: string; thumbnail?: string }[]
    }>('/workspace-library/list')
    return (body.entries ?? [])
      .filter((entry) => entry.previewKind === '3d-model')
      .map((entry) => ({
        workspacePath: entry.workspacePath,
        name: entry.workspacePath.split('/').pop() ?? entry.workspacePath,
        thumbnail: entry.thumbnail,
        previewKind: entry.previewKind,
        capability: entry.capability,
      }))
  } catch {
    return []
  }
}

const webRuntime = {
  python: {
    start: async () => {
      const result = await checkApi()
      return result.success && result.ready
        ? { success: true, port: Number(new URL(API_URL).port || window.location.port || 80) }
        : { success: false, error: result.success ? 'PolyKit server is still starting.' : result.error }
    },
    status: async () => {
      const result = await checkApi()
      return { ready: result.success && result.ready, apiUrl: API_URL }
    },
    onCrashed: (_cb: (data: { code: number | null }) => void) => {},
    offCrashed: () => {},
    onLog: (_cb: (line: string) => void) => {},
    offLog: () => {},
  },
  fs: {
    selectImage: () => chooseFile('image/*'),
    selectMeshFile: () => chooseFile('.glb,.gltf,.obj,.stl,.ply'),
    uploadImage: async (filePath: string) => uploadWorkspaceAsset(filePath),
    uploadMesh: async (filePath: string) => uploadWorkspaceAsset(filePath),
    listServerMeshes,
    saveModel: async (_defaultName: string) => null,
    readFileBase64: fileBase64,
    selectDirectory: async (_defaultPath?: string) => null,
    savePath: async (_args: unknown) => null,
    listDir: async (_dirPath: string) => [],
    listFiles: async (_dirPath: string, _extensions?: string[]) => [],
    selectTextFile: () => chooseFile('.txt,.json'),
    moveDirectory: async (_args: unknown) => ({ success: false, error: 'Directory operations are not available in the browser.' }),
    deleteDirectory: async (_dirPath: string) => ({ success: false, error: 'Directory operations are not available in the browser.' }),
  },
  settings: {
    get: async () => {
      try {
        const paths = await json<{ models_dir: string; workspace_dir: string; node_packs_dir?: string }>('/settings/paths')
        let proxy: ProxySettings | undefined = getStoredSettings().proxy
        try {
          proxy = await json<ProxySettings>('/settings/proxy')
        } catch {
          // Older server without the endpoint — keep the locally stored value.
        }
        let sources: DownloadSourceSettings | undefined = getStoredSettings().sources
        try {
          const remote = await json<{
            huggingface_endpoint?: string
            pypi_index_url?: string
            pytorch_index_url?: string
          }>('/settings/sources')
          sources = {
            huggingfaceEndpoint: remote.huggingface_endpoint ?? '',
            pypiIndexUrl: remote.pypi_index_url ?? '',
            pytorchIndexUrl: remote.pytorch_index_url ?? '',
          }
        } catch {
          // Older server without the endpoint — keep the locally stored value.
        }
        return {
          ...getStoredSettings(),
          modelsDir: paths.models_dir,
          workspaceDir: paths.workspace_dir,
          ...(paths.node_packs_dir ? { nodePacksDir: paths.node_packs_dir } : {}),
          proxy,
          sources,
        }
      } catch {
        return getStoredSettings()
      }
    },
    set: async (patch: Partial<WebSettings>) => {
      const next = setStoredSettings(patch)
      if (patch.modelsDir || patch.workspaceDir || patch.nodePacksDir) {
        await request('/settings/paths', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            models_dir: patch.modelsDir,
            workspace_dir: patch.workspaceDir,
            node_packs_dir: patch.nodePacksDir,
          }),
        }).catch(() => {})
      }
      if (patch.hfToken !== undefined) {
        await request('/settings/hf-token', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ token: patch.hfToken }),
        }).catch(() => {})
      }
      if (patch.proxy !== undefined) {
        const response = await request('/settings/proxy', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(patch.proxy),
        })
        if (!response.ok) throw new Error(`${response.status}: ${await response.text()}`)
      }
      if (patch.sources !== undefined) {
        const response = await request('/settings/sources', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            huggingface_endpoint: patch.sources.huggingfaceEndpoint ?? '',
            pypi_index_url: patch.sources.pypiIndexUrl ?? '',
            pytorch_index_url: patch.sources.pytorchIndexUrl ?? '',
          }),
        })
        if (!response.ok) throw new Error(`${response.status}: ${await response.text()}`)
      }
      return next
    },
    testProxy: async () => {
      try {
        const response = await request('/settings/proxy/test', { method: 'POST' })
        const body = await response.json().catch(() => ({})) as { ok?: boolean; error?: string }
        if (response.ok && body.ok) return { ok: true }
        return { ok: false, error: body.error ?? `HTTP ${response.status}` }
      } catch (error) {
        return { ok: false, error: error instanceof Error ? error.message : String(error) }
      }
    },
    testSources: async (kind = 'huggingface') => {
      try {
        const response = await request('/settings/sources/test', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ kind }),
        })
        const body = await response.json().catch(() => ({})) as { ok?: boolean; error?: string; url?: string }
        if (response.ok && body.ok) return { ok: true, url: body.url }
        return { ok: false, error: body.error ?? `HTTP ${response.status}`, url: body.url }
      } catch (error) {
        return { ok: false, error: error instanceof Error ? error.message : String(error) }
      }
    },
  },
  cache: {
    clear: async () => ({ success: true }),
  },
  api: {
    updatePaths: async (patch: { modelsDir?: string; workspaceDir?: string; nodePacksDir?: string }) => {
      await request('/settings/paths', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          models_dir: patch.modelsDir,
          workspace_dir: patch.workspaceDir,
          node_packs_dir: patch.nodePacksDir,
        }),
      }).catch(() => {})
      return { success: true }
    },
  },
  model: {
    export: async ({ outputUrl, format }: { outputUrl: string; format: string }) => {
      const path = outputUrl.replace(/^\/workspace\//, '')
      const response = await request(`/export/${encodeURIComponent(format)}?path=${encodeURIComponent(path)}`)
      if (!response.ok) return { success: false, error: await response.text() }
      downloadBlob(await response.blob(), `polykit-export.${format}`)
      return { success: true }
    },
    listDownloaded: async () => {
      const models = await json<Array<{ id: string; name: string; downloaded: boolean; vram_gb?: number }>>('/model/all')
      return models.filter((model) => model.downloaded).map((model) => ({ id: model.id, name: model.name, size_gb: model.vram_gb ?? 0 }))
    },
    activeDownloads: async () => {
      try {
        return await json<Array<{ modelId: string; percent: number; file?: string; fileIndex?: number; totalFiles?: number }>>('/model/hf-download/active')
      } catch {
        return []
      }
    },
    isDownloaded: async (modelId: string, downloadCheck?: string) => {
      const query = new URLSearchParams({ model_id: modelId })
      if (downloadCheck) query.set('download_check', downloadCheck)
      try {
        const result = await json<{ downloaded?: boolean }>(`/model/downloaded?${query.toString()}`)
        return result.downloaded === true
      } catch {
        return false
      }
    },
    download: streamModelDownload,
    pauseDownload: async (modelId: string) => json('/model/hf-download/pause', { method: 'POST', body: new URLSearchParams({ model_id: modelId }) }),
    cancelDownload: async (modelId: string) => json('/model/hf-download/cancel', { method: 'POST', body: new URLSearchParams({ model_id: modelId }) }),
    delete: async (modelId: string) => {
      const response = await request('/model/delete', { method: 'POST', body: new URLSearchParams({ model_id: modelId }) })
      return response.ok ? { success: true } : { success: false, error: await response.text() }
    },
    unloadAll: async () => {
      const response = await request('/model/unload-all', { method: 'POST' })
      return response.ok ? { success: true } : { success: false, error: await response.text() }
    },
    showInFolder: async (_modelId: string) => {},
    onProgress: (cb: (data: Record<string, unknown>) => void) => { modelProgressListeners.add(cb) },
    offProgress: () => { modelProgressListeners.clear() },
  },
  app: {
    info: async () => {
      const settings = getStoredSettings()
      return { userData: 'browser://local', modelsDir: settings.modelsDir, apiUrl: API_URL, platform: 'web', arch: 'browser' }
    },
    onError: (_cb: (message: string) => void) => {},
    offError: () => {},
  },
  log: {
    error: (message: string) => console.error('[PolyKit]', message),
    getPath: async () => 'browser://console',
    readAll: async (_session?: string) => ({}),
    listSessions: async () => [],
  },
  workspace: {
    listCollections: async () => ['Default'],
    createCollection: async (_name: string) => {},
    renameCollection: async (_oldName: string, _newName: string) => {},
    deleteCollection: async (_name: string) => {},
    listJobs: async (_collection: string) => [],
    saveJobMeta: async (_collection: string, _filename: string, _meta: unknown) => {},
    deleteJob: async (_collection: string, _filename: string) => {},
    library: {
      list: async () => json('/workspace-library/list'),
      read: async (request: unknown) => json('/workspace-library/read', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(request) }),
      open: async (request: unknown) => json('/workspace-library/open', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(request) }),
      delete: async (request: unknown) => json('/workspace-library/delete', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(request) }),
      rename: async (request: unknown) => json('/workspace-library/rename', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(request) }),
    },
  },
  setup: {
    check: async () => ({ needed: false, defaultDataDir: 'browser://local', platform: 'web', arch: 'browser' }),
    run: async () => ({ success: true }),
    saveDataDir: async (_baseDir: string) => {},
    onProgress: (_cb: (data: { step: string; percent: number; currentPackage?: string }) => void) => {},
    offProgress: () => {},
    onComplete: (_cb: () => void) => {},
    offComplete: () => {},
    onError: (_cb: (data: { message: string }) => void) => {},
    offError: () => {},
  },
  workflows: {
    export: async (workflow: Workflow) => {
      downloadBlob(new Blob([JSON.stringify(workflow, null, 2)], { type: 'application/json' }), `${workflow.name || 'workflow'}.json`)
      return { success: true }
    },
  },
  nodePacks: {
    list: async () => {
      try { return await json<unknown[]>('/node-packs/list') } catch { return [] }
    },
    installFromGitHub: async (_url: string) => ({ success: false, error: 'Node pack installation is not available from the Web client yet.' }),
    installFromLocal: async () => ({ success: false, cancelled: true }),
    uninstall: async (_nodePackId: string) => ({ success: false, error: 'Node pack removal is not available from the Web client yet.' }),
    repair: async (nodePackId: string) => {
      try {
        const response = await request(`/node-packs/setup/${encodeURIComponent(nodePackId)}`, { method: 'POST' })
        if (!response.ok) {
          const body = await response.json().catch(() => ({})) as { detail?: string }
          return { success: false, error: body.detail ?? `HTTP ${response.status}` }
        }
        return { success: true }
      } catch (error) {
        return { success: false, error: error instanceof Error ? error.message : String(error) }
      }
    },
    reload: async () => {
      try { await request('/node-packs/reload', { method: 'POST' }) } catch { /* best effort */ }
      return { success: true, errors: {} }
    },
    runProcess: async (_nodePackId: string, _input: unknown, _params: Record<string, unknown>) => ({ success: false, error: 'Process node packs are not exposed by the headless API yet.' }),
    onInstallProgress: (cb: (data: Record<string, unknown>) => void) => { installProgressListeners.add(cb) },
    offInstallProgress: () => { installProgressListeners.clear() },
  },
}

export function installWebRuntimeBridge(): void {
  Object.defineProperty(window, 'polykit', {
    configurable: true,
    value: webRuntime,
  })
}

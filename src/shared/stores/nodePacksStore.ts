import { create } from 'zustand'

import { translate } from '@shared/i18n'
import { useAppStore } from '@shared/stores/appStore'
import type { ModelNodePack, ProcessNodePack, AnyNodePack, NodePackNode } from '@shared/types/runtime.d'

// ─── Re-exports for consumers ─────────────────────────────────────────────────

export type { ModelNodePack, ProcessNodePack, AnyNodePack }

export interface NodePackModelResource {
  id: string
  name: string
  kind?: string
  repo?: string
  location?: string
  check?: string
  include_prefixes?: string[]
  skip_prefixes?: string[]
  required_for?: string[]
  size_note?: string
  note?: string
}

type NodePackLocaleText = {
  name?: string
  description?: string
}

type LocalizedNodePackNode = NodePackNode & {
  i18n?: Record<string, NodePackLocaleText>
}

export type ManagedModelNodePack = ModelNodePack & {
  models?: NodePackModelResource[]
  i18n?: Record<string, NodePackLocaleText>
  nodes: LocalizedNodePackNode[]
}

export function getNodePackModelResources(nodePack: ModelNodePack): NodePackModelResource[] {
  return (nodePack as ManagedModelNodePack).models ?? []
}

export type InstallStep = 'downloading' | 'extracting' | 'validating' | 'setting_up' | 'done' | 'error'

export interface InstallProgress {
  step:         InstallStep
  percent?:     number
  nodePackId?: string
  message?:     string
}

interface ServerNodeMetadata {
  id: string
  i18n?: Record<string, NodePackLocaleText>
}

interface ServerModelPackMetadata {
  id: string
  builtin?: boolean
  trusted?: boolean
  env?: 'shared' | 'isolated'
  requirements?: string[]
  download?: ModelNodePack['download']
  models?: NodePackModelResource[]
  i18n?: Record<string, NodePackLocaleText>
  nodes?: ServerNodeMetadata[]
  loadError?: string
}

function mergeNodeLocaleMetadata(
  nodes: NodePackNode[],
  serverNodes: ServerNodeMetadata[] | undefined,
): LocalizedNodePackNode[] {
  if (!serverNodes?.length) return nodes as LocalizedNodePackNode[]
  const byId = new Map(serverNodes.map((node) => [node.id, node]))
  return nodes.map((node) => {
    const serverNode = byId.get(node.id)
    if (!serverNode?.i18n) return node as LocalizedNodePackNode
    return { ...node, i18n: serverNode.i18n }
  })
}

async function mergeServerModelMetadata(list: AnyNodePack[]): Promise<{
  list: AnyNodePack[]
  loadErrors: Record<string, string>
}> {
  const apiUrl = useAppStore.getState().apiUrl
  if (!apiUrl) return { list, loadErrors: {} }

  try {
    const response = await fetch(`${apiUrl}/node-packs/list`)
    if (!response.ok) return { list, loadErrors: {} }
    const payload = await response.json() as unknown
    if (!Array.isArray(payload)) return { list, loadErrors: {} }

    const serverPacks = new Map<string, ServerModelPackMetadata>()
    for (const value of payload) {
      if (!value || typeof value !== 'object') continue
      const pack = value as ServerModelPackMetadata
      if (typeof pack.id !== 'string' || !pack.id) continue
      serverPacks.set(pack.id, pack)
    }

    const loadErrors: Record<string, string> = {}
    const merged = list.map((item): AnyNodePack => {
      if (item.type !== 'model') return item
      const server = serverPacks.get(item.id)
      if (!server) return item
      if (server.loadError) loadErrors[item.id] = server.loadError

      return {
        ...item,
        // FastAPI owns official-pack sync, so its source manifest is the
        // canonical authority for official/builtin identity. Do not trust a
        // third-party manifest's self-declared `trusted` flag in the browser.
        builtin: server.builtin ?? item.builtin,
        trusted: server.builtin ? true : item.trusted,
        env: server.env ?? item.env,
        requirements: server.requirements ?? item.requirements,
        download: server.download ?? item.download,
        models: server.models ?? getNodePackModelResources(item),
        // Presentation metadata remains separate from the stable source name,
        // description and node ids so language changes never mutate machine data.
        i18n: server.i18n ?? (item as ManagedModelNodePack).i18n,
        nodes: mergeNodeLocaleMetadata(item.nodes, server.nodes),
      } as ManagedModelNodePack
    })

    // The API inventory may contain an official isolated pack before the local
    // parser sees it (for example immediately after official sync). Keep the
    // local-compatible inventory as the primary source, but append missing
    // server model packs so Setup/Repair is never hidden just because its venv
    // is absent.
    for (const value of payload) {
      if (!value || typeof value !== 'object') continue
      const server = value as Partial<ManagedModelNodePack> & { id?: string; type?: string; loadError?: string }
      if (server.type !== 'model' || typeof server.id !== 'string' || !server.id) continue
      if (merged.some((item) => item.id === server.id)) continue
      const modelPack = server as ManagedModelNodePack
      merged.push({
        ...modelPack,
        trusted: modelPack.builtin ? true : Boolean(modelPack.trusted),
        nodes: Array.isArray(modelPack.nodes) ? modelPack.nodes : [],
      })
      if (server.loadError) loadErrors[server.id] = server.loadError
    }

    return { list: merged, loadErrors }
  } catch {
    // The cached list remains usable while the API is starting/restarting.
    return { list, loadErrors: {} }
  }
}

// ─── Store ────────────────────────────────────────────────────────────────────

interface ExtensionsStore {
  modelNodePacks:   ModelNodePack[]
  processNodePacks: ProcessNodePack[]
  loading:           boolean
  installProgress:   InstallProgress | null
  installError:      string | null
  loadErrors:        Record<string, string>

  loadNodePacks:    () => Promise<void>
  installFromGitHub: (url: string) => Promise<{ success: boolean; error?: string }>
  installFromLocal:  () => Promise<{ success: boolean; error?: string; cancelled?: boolean }>
  uninstall:         (nodePackId: string) => Promise<{ success: boolean; error?: string }>
  reload:            () => Promise<void>
  clearInstallState: () => void
}

export const useNodePacksStore = create<ExtensionsStore>((set, get) => ({
  modelNodePacks:   [],
  processNodePacks: [],
  loading:           false,
  installProgress:   null,
  installError:      null,
  loadErrors:        {},

  // ── Load list ──────────────────────────────────────────────────────────────

  async loadNodePacks() {
    set({ loading: true })
    try {
      const runtimeList = (await window.polykit.nodePacks.list()) as AnyNodePack[]
      const { list, loadErrors } = await mergeServerModelMetadata(runtimeList)
      set((state) => ({
        modelNodePacks:   list.filter((e): e is ModelNodePack   => e.type === 'model'),
        processNodePacks: list.filter((e): e is ProcessNodePack => e.type === 'process'),
        loadErrors:       { ...state.loadErrors, ...loadErrors },
        loading:          false,
      }))
    } catch {
      set({ loading: false })
    }
  },

  // ── Install from GitHub ────────────────────────────────────────────────────

  async installFromGitHub(url: string) {
    return installExtension(() => window.polykit.nodePacks.installFromGitHub(url), set)
  },

  // ── Install from local folder ──────────────────────────────────────────────

  async installFromLocal() {
    const result = await installExtension(() => window.polykit.nodePacks.installFromLocal(), set)
    // If user cancelled the folder picker, treat as a no-op (not an error)
    if ((result as any).cancelled) {
      set({ installProgress: null, installError: null })
      return { success: false, cancelled: true }
    }
    return result
  },

  // ── Uninstall ──────────────────────────────────────────────────────────────

  async uninstall(nodePackId: string) {
    const result = await window.polykit.nodePacks.uninstall(nodePackId)
    if (result.success) {
      set((state) => ({
        modelNodePacks:   state.modelNodePacks.filter((e)   => e.id !== nodePackId),
        processNodePacks: state.processNodePacks.filter((e) => e.id !== nodePackId),
      }))
    }
    return result
  },

  // ── Reload (rescan node-packs dir + Python registry) ──────────────────────

  async reload() {
    const result = await window.polykit.nodePacks.reload()
    if (result.success) {
      set({ loadErrors: result.errors ?? {} })
    }
    await get().loadNodePacks()
  },

  // ── Helpers ────────────────────────────────────────────────────────────────

  clearInstallState() {
    set({ installProgress: null, installError: null })
  },
}))

function installationFallback(): string {
  return translate('nodePacks.installationFailed', useAppStore.getState().language)
}

async function installExtension(
  invoke: () => Promise<{ success: boolean; error?: string; extension?: AnyNodePack; nodePackId?: string }>,
  set: (partial: Partial<ExtensionsStore> | ((state: ExtensionsStore) => Partial<ExtensionsStore>)) => void,
) {
    set({ installProgress: { step: 'downloading', percent: 0 }, installError: null })

    window.polykit.nodePacks.onInstallProgress((data) => {
      if (data.step === 'error') {
        set({ installProgress: null, installError: data.message ?? installationFallback() })
      } else {
        set({ installProgress: data as InstallProgress })
      }
    })

    try {
      const result = await invoke()

      if (result.success && result.extension) {
        const ext = result.extension as AnyNodePack
        set((state) => {
          if (ext.type === 'process') {
            const filtered = state.processNodePacks.filter((e) => e.id !== ext.id)
            return {
              processNodePacks: [...filtered, ext],
              installProgress:   { step: 'done', nodePackId: result.nodePackId },
              installError:      null,
            }
          } else {
            const filtered = state.modelNodePacks.filter((e) => e.id !== ext.id)
            return {
              modelNodePacks: [...filtered, ext],
              installProgress: { step: 'done', nodePackId: result.nodePackId },
              installError:    null,
            }
          }
        })
      } else {
        set({ installProgress: null, installError: result.error ?? installationFallback() })
      }

      return result
    } catch (err) {
      const error = String(err)
      set({ installProgress: null, installError: error })
      return { success: false, error }
    } finally {
      window.polykit.nodePacks.offInstallProgress()
    }
}

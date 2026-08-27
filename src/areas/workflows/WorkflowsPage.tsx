import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  ReactFlow,
  ReactFlowProvider,
  Background,

  addEdge,
  useNodesState,
  useEdgesState,
  useReactFlow,
  type Connection,
  type Node,
  type Edge,
  type OnConnectStartParams,
} from '@xyflow/react'
import { FolderPlus } from 'lucide-react'
import { useWorkflowsStore, NODE_TYPES_WITHOUT_TARGET, NODE_TYPES_WITHOUT_SOURCE, FOLDER_COLORS } from '@shared/stores/workflowsStore'
import { useNodePacksStore } from '@shared/stores/nodePacksStore'
import { useAppStore } from '@shared/stores/appStore'
import { useNavStore } from '@shared/stores/navStore'
import { useI18n, type TranslationKey } from '@shared/i18n'
import {
  Button,
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  Input,
} from '@shared/components/ui'
import type { Workflow, WFNode, WFEdge, WFNodeData } from '@shared/types/runtime.d'
import { buildAllWorkflowNodePacks } from './mockNodePacks'
import { fetchWorkflowNodePacks } from './workflowNodePacks'
import type { WorkflowNodePack } from './mockNodePacks'
import { instantiateWorkflowTemplate, getWorkflowTemplates, type WorkflowTemplate } from './workflowTemplates'
import { useWorkflowRunStore } from './workflowRunStore'
import { validateWorkflowPreflight } from './preflight'
import NodePackNode    from './nodes/NodePackNode'
import ImageNode        from './nodes/ImageNode'
import TextNode         from './nodes/TextNode'
import OutputNode       from './nodes/OutputNode'
import Load3DMeshNode   from './nodes/Load3DMeshNode'
import PreviewImageNode from './nodes/PreviewImageNode'
import NoteNode         from './nodes/NoteNode'
import WorkflowEdge     from './nodes/WorkflowEdge'

// ─── Constants ────────────────────────────────────────────────────────────────

const DRAG_KEY      = 'polykit/node-pack-id'
const DRAG_NODE_KEY = 'polykit/node-type'
const WORKFLOW_DRAG_KEY = 'polykit/workflow-id'
const TAB_DRAG_KEY = 'polykit/tab-id'

function readDragData(dataTransfer: DataTransfer, key: string): string {
  return dataTransfer.getData(key)
}
const NODE_TYPES = { nodePackNode: NodePackNode, imageNode: ImageNode, textNode: TextNode, outputNode: OutputNode, meshNode: Load3DMeshNode, previewNode: PreviewImageNode, noteNode: NoteNode }

// Loop-container node types: resizable frames whose children form a loop body.
const EDGE_TYPES = { workflowEdge: WorkflowEdge }

const DEFAULT_EDGE_OPTS = { type: 'workflowEdge' }


// ─── IO badge ─────────────────────────────────────────────────────────────────

const IO_STYLES: Record<'image' | 'text' | 'mesh' | 'audio', string> = {
  audio: 'bg-sky-500/15 text-sky-400 border-sky-500/25',
  image: 'bg-sky-500/15 text-sky-400 border-sky-500/25',
  mesh:  'bg-primary/15 text-primary border-primary/25',
  text:  'bg-amber-500/15 text-amber-400 border-amber-500/25',
}

function IoBadge({ type }: { type: 'image' | 'text' | 'mesh' | 'audio' }) {
  const { t } = useI18n()
  const labels: Record<typeof type, TranslationKey> = {
    image: 'workflows.typeImage',
    text: 'workflows.typeText',
    mesh: 'workflows.typeMesh',
    audio: 'workflows.typeAudio',
  }
  return (
    <span className={`inline-flex items-center px-1.5 py-0.5 rounded text-[9px] font-medium border ${IO_STYLES[type]}`}>
      {t(labels[type])}
    </span>
  )
}

function NodePackInputBadges({ nodePack }: { nodePack: WorkflowNodePack }) {
  const inputs = nodePack.inputs?.length ? nodePack.inputs : [nodePack.input]
  return (
    <>
      {inputs.map((type, index) => (
        <span key={`${type}-${index}`} className="inline-flex items-center gap-1">
          {index > 0 && <span className="text-[9px] text-muted-foreground">+</span>}
          <IoBadge type={type} />
        </span>
      ))}
    </>
  )
}

// ─── Helpers ──────────────────────────────────────────────────────────────────

function newId(): string { return crypto.randomUUID() }

// Node clipboard (module-level so Ctrl+C in one workflow tab can be pasted in
// another — the canvas remounts per tab but the module survives).
const _nodeClipboard: { current: { nodes: Node[]; edges: Edge[]; pastes: number } | null } = { current: null }

function newWorkflow(nodePack: WorkflowNodePack | undefined, translate?: (key: TranslationKey, params?: Record<string, string | number>) => string): Workflow {
  const now = new Date().toISOString()
  const newWorkflowName = translate?.('workflows.newWorkflow') ?? 'New Workflow'
  if (!nodePack) {
    return { id: newId(), name: newWorkflowName, description: '', nodes: [], edges: [], createdAt: now, updatedAt: now }
  }

  const inputTypes = nodePack.inputs?.length ? nodePack.inputs : [nodePack.input]
  const inputNodeType = (input: WorkflowNodePack['input']): string | null => input === 'text'
    ? 'textNode'
    : input === 'mesh'
      ? 'meshNode'
      : input === 'image'
        ? 'imageNode'
        : null
  const outputType = nodePack.output === 'mesh'
    ? 'outputNode'
    : nodePack.output === 'image'
      ? 'previewNode'
      : null
  const nodePackNodeId = newId()
  const outputId = outputType ? newId() : null
  const inputNodes = inputTypes.map((input, index) => {
    const type = inputNodeType(input)
    if (!type) return null
    return {
      id: newId(),
      type,
      position: { x: 80, y: 130 + index * 150 },
      data: { enabled: true, params: {} },
    } as WFNode
  }).filter((node): node is WFNode => node !== null)
  const nodes: WFNode[] = [
    ...inputNodes,
    {
      id: nodePackNodeId,
      type: 'nodePackNode',
      position: { x: inputNodes.length > 0 ? 360 : 220, y: 180 },
      data: { nodePackId: nodePack.id, enabled: true, params: {} },
    },
    ...(outputType && outputId ? [{
      id: outputId,
      type: outputType,
      position: { x: 680, y: 180 },
      data: { enabled: true, params: {} },
    } as WFNode] : []),
  ]
  const edges: WFEdge[] = [
    ...inputNodes.map((node, index) => ({
      id: `e-${node.id}-${nodePackNodeId}`,
      source: node.id,
      target: nodePackNodeId,
      targetHandle: `input-${index}`,
    })),
    ...(outputId ? [{
      id: `e-${nodePackNodeId}-${outputId}`,
      source: nodePackNodeId,
      sourceHandle: 'output',
      target: outputId,
    }] : []),
  ]
  return {
    id: newId(),
    name: `${nodePack.name} ${newWorkflowName}`,
    description: translate?.('workflows.workflowUsing', { name: nodePack.name }) ?? `Workflow using ${nodePack.name}.`,
    nodes,
    edges,
    createdAt: now,
    updatedAt: now,
  }
}

// ─── Node packs panel ────────────────────────────────────────────────────────

const PANEL_MIN = 240
const PANEL_MAX = 860

const PANEL_BUILTIN_NODES = [
  { type: 'imageNode',   labelKey: 'workflows.nodeImage' as TranslationKey, color: '#38bdf8', icon: <><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><polyline points="21 15 16 10 5 21"/></> },
  { type: 'textNode',    labelKey: 'workflows.nodeText' as TranslationKey, color: '#fbbf24', icon: <><path d="M17 6.1H3M21 12.1H3M15.1 18H3"/></> },
  { type: 'meshNode',    labelKey: 'workflows.nodeMesh' as TranslationKey, color: '#5d94d9', icon: <><path d="M12 2L2 7l10 5 10-5-10-5zM2 17l10 5 10-5M2 12l10 5 10-5"/></> },
  { type: 'outputNode',  labelKey: 'workflows.nodeOutput' as TranslationKey, color: '#5680b8', icon: <><path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"/></> },
  { type: 'previewNode', labelKey: 'workflows.nodePreview' as TranslationKey, color: '#5680b8', icon: <><rect x="3" y="3" width="8" height="8" rx="1"/><rect x="13" y="3" width="8" height="8" rx="1"/><rect x="3" y="13" width="8" height="8" rx="1"/><rect x="13" y="13" width="8" height="8" rx="1"/></> },
  { type: 'noteNode',    labelKey: 'workflows.nodeNote' as TranslationKey, color: '#a1a1aa', icon: <><path d="M4 5a1 1 0 0 1 1-1h14a1 1 0 0 1 1 1v10a2 2 0 0 1-1 1H9l-4 4V5z"/><line x1="8" y1="9" x2="16" y2="9"/><line x1="8" y1="13" x2="13" y2="13"/></> },
]

function ExtGroupHeader({ title, author, expanded, onToggle, count }: { title: string; author?: string; expanded: boolean; onToggle: () => void; count: number }) {
  return (
    <button
      onClick={onToggle}
      className="flex items-center gap-2 w-full px-1 py-1.5 group"
    >
      <svg
        width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"
        className="shrink-0 text-muted-foreground transition-colors group-hover:text-foreground"
        style={{ transform: expanded ? 'rotate(90deg)' : 'rotate(0deg)', transition: 'transform 0.15s ease' }}
      >
        <polyline points="9 18 15 12 9 6"/>
      </svg>
      <div className="flex flex-col items-start min-w-0">
        <span className="truncate text-[11px] font-semibold leading-tight text-foreground transition-colors group-hover:text-foreground">{title}</span>
        {author && <span className="truncate text-[9px] leading-tight text-muted-foreground">{author}</span>}
      </div>
      <span className="ml-auto shrink-0 text-[9px] text-muted-foreground">{count}</span>
    </button>
  )
}

function NodeLibraryPanel({ allNodePacks, open, onUseTemplate }: {
  allNodePacks: WorkflowNodePack[]
  open: boolean
  onUseTemplate: (template: WorkflowTemplate) => void
}) {
  const { t } = useI18n()
  const [search, setSearch]       = useState('')
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({})
  const [width, setWidth]         = useState(288)
  const dragging = useRef(false)
  const startX   = useRef(0)
  const startW   = useRef(0)

  useEffect(() => {
    const onMove = (e: MouseEvent) => {
      if (!dragging.current) return
      const delta = startX.current - e.clientX
      setWidth(() => Math.min(PANEL_MAX, Math.max(PANEL_MIN, startW.current - delta)))
    }
    const onUp = () => { dragging.current = false; document.body.style.cursor = '' }
    document.addEventListener('mousemove', onMove)
    document.addEventListener('mouseup',   onUp)
    return () => {
      document.removeEventListener('mousemove', onMove)
      document.removeEventListener('mouseup',   onUp)
    }
  }, [])

  const cols      = width >= 580 ? 3 : width >= 370 ? 2 : 1
  const gridClass = cols === 3 ? 'grid-cols-3' : cols === 2 ? 'grid-cols-2' : 'grid-cols-1'
  const query     = search.trim().toLowerCase()

  const toggleGroup = (id: string) => setCollapsed((c) => ({ ...c, [id]: !c[id] }))
  const isExpanded  = (id: string, hasMatches: boolean) => (query && hasMatches) || !collapsed[id]

  // Base group
  const filteredBuiltinNodes = PANEL_BUILTIN_NODES.filter((n) => !query || t(n.labelKey).toLowerCase().includes(query))
  const filteredBuiltinExts  = allNodePacks.filter((e) => e.builtin && (!query || e.name.toLowerCase().includes(query)))
  const baseCount            = filteredBuiltinNodes.length + filteredBuiltinExts.length
  const baseVisible          = !query || baseCount > 0

  // Installed node-pack ids — used to gate templates that need them installed.
  const installedPackIds = useMemo(
    () => new Set(allNodePacks.map((ext) => ext.nodePackId)),
    [allNodePacks],
  )
  const navigate = useNavStore((s) => s.navigate)

  // Non-builtin groups: grouped by node pack
  const nonBuiltinMap = useMemo(() => {
    const map = new Map<string, { nodePackName: string; nodes: WorkflowNodePack[] }>()
    for (const ext of allNodePacks) {
      if (ext.builtin) continue
      if (!map.has(ext.nodePackId)) map.set(ext.nodePackId, { nodePackName: ext.nodePackName, nodes: [] })
      map.get(ext.nodePackId)!.nodes.push(ext)
    }
    return map
  }, [allNodePacks])

  // Templates are validated workflows, independent of any single node pack.
  const allTemplates = getWorkflowTemplates()
  const filteredTemplates = allTemplates.filter(
    (template) => !query || `${template.name} ${template.description}`.toLowerCase().includes(query),
  )
  const templatesVisible = !query || filteredTemplates.length > 0

  return (
    <div
      style={{ width: open ? width : 0 }}
      className="flex shrink-0 order-first overflow-hidden border-r border-border/40 bg-card/45 transition-[width] duration-300 ease-in-out"
    >
      <div className="flex shrink-0" style={{ width }}>
        <div className="flex min-w-0 flex-1 flex-col bg-card/55">

          {/* Header */}
          <div className="bg-card/35 px-4 py-4">
            <div className="flex items-center gap-2">
              <span className="flex items-center justify-center w-5 h-5 rounded-md bg-sky-500/10 border border-sky-500/20 text-sky-400">
                <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
                  <rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/>
                  <rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/>
                </svg>
              </span>
              <div className="min-w-0">
                <h2 className="text-xs font-semibold text-foreground">{t('workflows.nodes')}</h2>
                <p className="mt-0.5 text-[10px] text-muted-foreground">{t('workflows.nodeLibraryHint')}</p>
              </div>
            </div>
          </div>

          {/* Search */}
          <div className="bg-card/20 px-3.5 pb-2.5 pt-3">
            <div className="flex items-center gap-2 rounded-md border border-input bg-muted/70 px-2.5 py-1.5 focus-within:border-ring">
              <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="shrink-0 text-muted-foreground">
                <circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/>
              </svg>
              <input
                type="text"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder={t('workflows.searchShort')}
                className="min-w-0 flex-1 bg-transparent text-[11px] text-foreground placeholder:text-muted-foreground focus:outline-none"
              />
              {search && (
                <button onClick={() => setSearch('')} className="text-muted-foreground transition-colors hover:text-foreground">
                  <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
                    <line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>
                  </svg>
                </button>
              )}
            </div>
          </div>

          {/* Groups */}
          <div className="flex-1 overflow-y-auto px-3 py-2.5 flex flex-col gap-1">

            {/* ── Base group ── */}
            {baseVisible && (
              <div>
                <ExtGroupHeader
                  title={t('workflows.base')}
                  expanded={isExpanded('base', baseCount > 0)}
                  onToggle={() => toggleGroup('base')}
                  count={baseCount}
                />
                {isExpanded('base', baseCount > 0) && (
                  <div className={`grid ${gridClass} gap-2.5 mt-2 mb-4`}>
                    {filteredBuiltinNodes.map(({ type, labelKey, color, icon }) => (
                      <div
                        key={type}
                        draggable
                        onDragStart={(e) => { e.dataTransfer.setData(DRAG_NODE_KEY, type); e.dataTransfer.effectAllowed = 'copy' }}
                        className="flex cursor-grab flex-col gap-2 rounded-md border border-border/55 bg-card/75 px-3.5 py-3.5 transition-colors hover:border-primary/30 hover:bg-muted/60 active:cursor-grabbing"
                      >
                        <div className="flex items-center gap-2">
                          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke={color} strokeWidth="2" className="shrink-0">{icon}</svg>
                          <p className="truncate text-xs font-semibold text-foreground">{t(labelKey)}</p>
                        </div>
                      </div>
                    ))}
                    {filteredBuiltinExts.map((ext) => (
                      <div
                        key={ext.id}
                        draggable
                        onDragStart={(e) => { e.dataTransfer.setData(DRAG_KEY, ext.id); e.dataTransfer.effectAllowed = 'copy' }}
                        className="flex cursor-grab flex-col gap-2 rounded-md border border-border/55 bg-card/75 px-3.5 py-3.5 transition-colors hover:border-primary/30 hover:bg-muted/60 active:cursor-grabbing"
                      >
                        <p className="truncate text-xs font-semibold text-foreground">{ext.name}</p>
                        <div className="flex items-center gap-1 mt-auto">
                          <NodePackInputBadges nodePack={ext} />
                          <svg width="7" height="7" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="shrink-0 text-muted-foreground">
                            <path d="M5 12h14M13 6l6 6-6 6"/>
                          </svg>
                          <IoBadge type={ext.output} />
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}

            {/* ── Templates (validated workflows) ── */}
            {templatesVisible && (
              <div>
                <ExtGroupHeader
                  title={t('workflows.templates')}
                  expanded={isExpanded('templates', filteredTemplates.length > 0)}
                  onToggle={() => toggleGroup('templates')}
                  count={allTemplates.length}
                />
                {isExpanded('templates', filteredTemplates.length > 0) && (
                  <div className={`grid ${gridClass} gap-2.5 mt-2 mb-4`}>
                    {filteredTemplates.map((template) => {
                      const missing = (template.requires ?? []).filter((id) => !installedPackIds.has(id))
                      return missing.length === 0 ? (
                        <button
                          key={template.templateId}
                          onClick={() => onUseTemplate(template)}
                          title={template.description}
                          className="flex flex-col gap-2 rounded-lg border border-primary/30 bg-primary/5 px-4 py-3.5 text-left transition-colors hover:border-primary/50 hover:bg-primary/10"
                        >
                          <div className="flex items-center gap-2">
                            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" className="shrink-0">
                              <path d="M12 3v18M3 12h18M5.5 5.5l13 13M18.5 5.5l-13 13" />
                            </svg>
                            <p className="truncate text-xs font-semibold text-primary">{template.name}</p>
                          </div>
                          <p className="line-clamp-2 text-[10px] leading-relaxed text-primary/70">{template.description}</p>
                          <span className="mt-auto self-start rounded border border-primary/30 bg-primary/10 px-1.5 py-0.5 text-[9px] font-medium text-primary">{t('workflows.template')}</span>
                        </button>
                      ) : (
                        <button
                          key={template.templateId}
                          onClick={() => navigate('nodePacks')}
                          title={t('workflows.installHint', { nodes: missing.join(', ') })}
                          className="flex flex-col gap-2 px-3 py-3 text-left rounded-lg border border-amber-500/30 bg-amber-500/5 transition-colors hover:bg-amber-500/10 hover:border-amber-400/40"
                        >
                          <p className="text-xs font-semibold text-amber-100 truncate">{template.name}</p>
                          <p className="text-[10px] text-amber-200/70 leading-relaxed line-clamp-2">
                            {t('workflows.installHint', { nodes: missing.join(', ') })}
                          </p>
                          <span className="mt-auto px-1.5 py-0.5 self-start rounded text-[9px] font-medium border border-amber-500/30 bg-amber-500/10 text-amber-300">{t('workflows.install')}</span>
                        </button>
                      )
                    })}
                  </div>
                )}
              </div>
            )}

            {/* ── Non-builtin node pack groups ── */}
            {[...nonBuiltinMap.entries()].map(([extId, { nodePackName, nodes }]) => {
              const filtered = nodes.filter((e) => !query || e.name.toLowerCase().includes(query))
              if (query && filtered.length === 0) return null
              const displayNodes = query ? filtered : nodes
              const expanded = isExpanded(extId, filtered.length > 0)

              return (
                <div key={extId}>
                  <ExtGroupHeader
                    title={nodePackName}
                    author={displayNodes[0]?.nodePackAuthor}
                    expanded={expanded}
                    onToggle={() => toggleGroup(extId)}
                    count={displayNodes.length}
                  />
                  {expanded && (
                    <div className={`grid ${gridClass} gap-2.5 mt-2 mb-4`}>
                      {displayNodes.map((ext) => (
                        <div
                          key={ext.id}
                          draggable
                          onDragStart={(e) => { e.dataTransfer.setData(DRAG_KEY, ext.id); e.dataTransfer.effectAllowed = 'copy' }}
                          className="flex cursor-grab flex-col gap-2 rounded-md border border-border/55 bg-card/75 px-3.5 py-3.5 transition-colors hover:border-primary/30 hover:bg-muted/60 active:cursor-grabbing"
                        >
                          <p className="truncate text-xs font-semibold text-foreground">{ext.name}</p>
                          {ext.description && cols === 1 && (
                            <p className="line-clamp-2 text-[10px] leading-relaxed text-muted-foreground">{ext.description}</p>
                          )}
                          <div className="flex items-center gap-1 mt-auto">
                            <NodePackInputBadges nodePack={ext} />
                          <svg width="7" height="7" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="shrink-0 text-muted-foreground">
                              <path d="M5 12h14M13 6l6 6-6 6"/>
                            </svg>
                            <IoBadge type={ext.output} />
                          </div>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              )
            })}

            {/* Empty state */}
            {query && baseCount === 0 && filteredTemplates.length === 0 && [...nonBuiltinMap.values()].every((g) =>
              !g.nodes.some((e) => e.name.toLowerCase().includes(query))
            ) && (
              <p className="pt-4 text-center text-[11px] text-muted-foreground">{t('workflows.noResults', { query })}</p>
            )}

          </div>
        </div>

        {/* Resize handle — the canvas-side edge of the left library */}
        <div
          onMouseDown={(e) => {
            dragging.current = true; startX.current = e.clientX; startW.current = width
            document.body.style.cursor = 'col-resize'; e.preventDefault()
          }}
          className="w-1 shrink-0 cursor-col-resize self-stretch transition-colors hover:bg-muted-foreground active:bg-primary/60"
        />
      </div>
    </div>
  )
}

function outputDisplayName(url: string): string {
  const raw = url.split('/').pop() ?? 'output.glb'
  try { return decodeURIComponent(raw) } catch { return raw }
}

interface RemoteWorkflowRun {
  run_id: string
  status: string
  progress?: number
  step?: string
  output_url?: string
  error?: string
  meta?: Record<string, unknown>
}

function WorkflowOutputsPanel({ workflowId }: { workflowId?: string }): JSX.Element {
  const { t } = useI18n()
  const currentJob = useAppStore((s) => s.currentJob)
  const meshHistory = useAppStore((s) => s.meshHistory)
  const setCurrentJob = useAppStore((s) => s.setCurrentJob)
  const apiUrl = useAppStore((s) => s.apiUrl)
  const { navigate } = useNavStore()
  const [remoteRuns, setRemoteRuns] = useState<RemoteWorkflowRun[]>([])

  // The server owns the actual generation job. Poll its persisted run list so
  // a browser refresh can reconnect to progress and outputs instead of showing
  // a fresh empty client state.
  useEffect(() => {
    if (!apiUrl) {
      setRemoteRuns([])
      return
    }

    let disposed = false
    const loadRuns = async (): Promise<void> => {
      try {
        const params = new URLSearchParams({ limit: '20', collection: 'Workflows' })
        if (workflowId) params.set('workflow_id', workflowId)
        const response = await fetch(`${apiUrl}/workflow-runs?${params.toString()}`)
        if (!response.ok) return
        const data = await response.json() as unknown
        if (!disposed && Array.isArray(data)) setRemoteRuns(data as RemoteWorkflowRun[])
      } catch {
        // The canvas remains usable while the API is temporarily unavailable.
      }
    }

    void loadRuns()
    const timer = window.setInterval(() => { void loadRuns() }, 1500)
    return () => {
      disposed = true
      window.clearInterval(timer)
    }
  }, [apiUrl, workflowId])

  const activeRemoteRun = remoteRuns.find((run) => run.status === 'pending' || run.status === 'running')
  const latestRemoteOutput = remoteRuns.find((run) => run.status === 'done' && run.output_url)

  // If the browser was refreshed, restore the latest server output into the
  // shared viewer state as soon as the run becomes terminal.
  useEffect(() => {
    const url = latestRemoteOutput?.output_url
    if (!url || currentJob?.status === 'generating') return
    if (currentJob?.outputUrl === url) return
    const existing = useAppStore.getState().currentJob
    setCurrentJob({
      ...(existing ?? {
        id: `workflow-reconnected-${latestRemoteOutput?.run_id ?? Date.now()}`,
        imageFile: '__workflow__',
        createdAt: Date.now(),
      }),
      status: 'done',
      progress: 100,
      outputUrl: url,
    })
    useAppStore.getState().pushMeshUrl(url)
  }, [currentJob?.outputUrl, currentJob?.status, latestRemoteOutput?.output_url, latestRemoteOutput?.run_id, setCurrentJob])

  const outputs = useMemo(() => {
    const candidates = [
      ...remoteRuns.map((run) => run.output_url),
      currentJob?.outputUrl,
      ...[...meshHistory].reverse(),
    ]
    return [...new Set(candidates.filter((url): url is string => typeof url === 'string' && url.length > 0))]
  }, [currentJob?.outputUrl, meshHistory, remoteRuns])

  const openOutput = (url: string): void => {
    const existing = useAppStore.getState().currentJob
    setCurrentJob({
      ...(existing ?? {
        id: `workflow-output-${Date.now()}`,
        imageFile: '__workflow__',
        createdAt: Date.now(),
      }),
      status: 'done',
      progress: 100,
      outputUrl: url,
    })
    navigate('assets')
  }

  const isRunning = !!activeRemoteRun || currentJob?.status === 'uploading' || currentJob?.status === 'generating'
  const progress = activeRemoteRun?.progress ?? currentJob?.progress ?? 0
  const step = activeRemoteRun?.step ?? currentJob?.step ?? t('common.processing')

  return (
    <aside className="flex w-[292px] shrink-0 flex-col border-l border-border/40 bg-card/55">
      <div className="bg-card/35 px-4 py-4">
        <div className="flex items-center gap-2">
          <span className="flex h-5 w-5 items-center justify-center rounded-md border border-primary/20 bg-primary/10 text-primary">
            <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
              <path d="M4 7h16M4 12h16M4 17h10"/><circle cx="19" cy="17" r="2"/>
            </svg>
          </span>
          <div className="min-w-0">
            <h2 className="text-xs font-semibold text-foreground">{t('workflows.outputs')}</h2>
            <p className="mt-0.5 text-[10px] text-muted-foreground">{t('workflows.generatedProducts')}</p>
          </div>
          {outputs.length > 0 && <span className="ml-auto text-[10px] text-muted-foreground">{outputs.length}</span>}
        </div>
      </div>

      <div className="flex-1 space-y-3 overflow-y-auto p-3">
        {isRunning && (
          <div className="rounded-xl border border-primary/25 bg-primary/5 p-3">
            <div className="flex items-center gap-2">
              <svg className="animate-spin text-primary" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
                <path d="M21 12a9 9 0 1 1-6.219-8.56" />
              </svg>
              <span className="text-[11px] font-medium text-foreground">{t('workflows.running')}</span>
              <span className="ml-auto text-[10px] text-primary">{progress}%</span>
            </div>
            <div className="mt-2 h-1 overflow-hidden rounded-full bg-muted">
              <div className="h-full rounded-full bg-primary transition-[width]" style={{ width: `${Math.max(3, progress)}%` }} />
            </div>
            <p className="mt-2 truncate text-[10px] text-muted-foreground">{step}</p>
          </div>
        )}

        {outputs.length === 0 ? (
          <div className="flex flex-col items-center justify-center px-4 py-16 text-center">
            <div className="mb-3 flex h-10 w-10 items-center justify-center rounded-lg border border-dashed border-border text-muted-foreground">
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.4">
                <path d="m12 3 8 4.5v9L12 21l-8-4.5v-9L12 3Z"/><path d="m4 7.5 8 4.5 8-4.5M12 12v9"/>
              </svg>
            </div>
            <p className="text-xs text-muted-foreground">{isRunning ? t('workflows.generatingFinalOutput') : t('workflows.noOutputs')}</p>
            <p className="mt-1 text-[10px] leading-relaxed text-muted-foreground/70">
              {isRunning
                ? t('workflows.finalMeshWillAppear')
                : t('workflows.runToGenerate')}
            </p>
          </div>
        ) : (
          <div className="space-y-2.5">
            {outputs.map((url, index) => {
              const active = currentJob?.outputUrl === url
              return (
                <button
                  key={url}
                  type="button"
                  onClick={() => openOutput(url)}
                  className={`w-full text-left rounded-lg border p-3 transition-colors group
                    ${active
                      ? 'border-primary/45 bg-primary/10'
                      : 'border-border/55 bg-card/70 hover:border-primary/30 hover:bg-muted/60'}`}
                >
                  <div className="flex items-start gap-2.5">
                    <span className={`flex items-center justify-center w-8 h-8 rounded-lg shrink-0 border
                      ${active ? 'border-primary/30 bg-primary/15 text-primary' : 'border-border bg-muted text-muted-foreground group-hover:text-foreground'}`}>
                      <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
                        <path d="m12 3 8 4.5v9L12 21l-8-4.5v-9L12 3Z"/><path d="m4 7.5 8 4.5 8-4.5M12 12v9"/>
                      </svg>
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="flex items-center gap-1.5">
                        <span className="truncate text-[11px] font-medium text-foreground">{outputDisplayName(url)}</span>
                        {index === 0 && <span className="shrink-0 text-[9px] text-sky-400">{t('workflows.latest')}</span>}
                      </span>
                      <span className="mt-1 block truncate text-[10px] text-muted-foreground">{url.replace(/^\/workspace\//, '')}</span>
                    </span>
                  </div>
                  <span className="mt-2.5 flex items-center justify-between border-t border-border pt-2">
                    <span className="text-[9px] uppercase tracking-wider text-muted-foreground">{t('workflows.mesh3d')}</span>
                    <span className="text-[10px] text-primary opacity-0 transition-opacity group-hover:opacity-100">{t('workflows.viewIn3d')}</span>
                  </span>
                </button>
              )
            })}
          </div>
        )}
      </div>
    </aside>
  )
}

// ─── Page ─── toggle button icon ─────────────────────────────────────────────

function PanelToggleIcon({ open }: { open: boolean }) {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"
      style={{ transition: 'transform 0.3s ease', transform: open ? 'rotate(0deg)' : 'rotate(180deg)' }}>
      <rect x="3" y="3" width="18" height="18" rx="2"/>
      <line x1="15" y1="3" x2="15" y2="21"/>
    </svg>
  )
}

// ─── Node palette (Space to open) ────────────────────────────────────────────

const BUILTIN_NODES = [
  { type: 'imageNode',   labelKey: 'workflows.nodeImage' as TranslationKey, descriptionKey: 'workflows.nodeImageDescription' as TranslationKey, color: '#38bdf8' },
  { type: 'textNode',    labelKey: 'workflows.nodeText' as TranslationKey, descriptionKey: 'workflows.nodeTextDescription' as TranslationKey, color: '#fbbf24' },
  { type: 'meshNode',    labelKey: 'workflows.nodeMesh' as TranslationKey, descriptionKey: 'workflows.nodeMeshDescription' as TranslationKey, color: '#5d94d9' },
  { type: 'outputNode',  labelKey: 'workflows.nodeOutput' as TranslationKey, descriptionKey: 'workflows.nodeOutputDescription' as TranslationKey, color: '#5680b8' },
  { type: 'previewNode', labelKey: 'workflows.nodePreview' as TranslationKey, descriptionKey: 'workflows.nodePreviewDescription' as TranslationKey, color: '#5680b8' },
  { type: 'noteNode',    labelKey: 'workflows.nodeNote' as TranslationKey, descriptionKey: 'workflows.nodeNoteDescription' as TranslationKey, color: '#a1a1aa' },
]

type PaletteItem =
  | { kind: 'node'; data: typeof BUILTIN_NODES[0] }
  | { kind: 'ext';  data: WorkflowNodePack }

type PaletteGroup = {
  id:       string
  title:    string
  author?:  string
  expanded: boolean
  items:    Array<PaletteItem & { flatIdx: number }>
}

function NodePalette({
  allNodePacks,
  onSelect,
  onClose,
}: {
  allNodePacks: WorkflowNodePack[]
  onSelect: (type: string, nodePackId?: string) => void
  onClose: () => void
}) {
  const { t } = useI18n()
  const [query,       setQuery]       = useState('')
  const [collapsed,   setCollapsed]   = useState<Record<string, boolean>>({})
  const [activeIndex, setActiveIndex] = useState(0)
  const inputRef = useRef<HTMLInputElement>(null)

  const q = query.trim().toLowerCase()

  const nonBuiltinMap = useMemo(() => {
    const map = new Map<string, { nodePackName: string; nodePackAuthor: string; nodes: WorkflowNodePack[] }>()
    for (const ext of allNodePacks) {
      if (ext.builtin) continue
      if (!map.has(ext.nodePackId)) map.set(ext.nodePackId, { nodePackName: ext.nodePackName, nodePackAuthor: ext.nodePackAuthor, nodes: [] })
      map.get(ext.nodePackId)!.nodes.push(ext)
    }
    return map
  }, [allNodePacks])

  const toggleGroup = (id: string) => setCollapsed((c) => ({ ...c, [id]: !c[id] }))
  const isExpanded  = (id: string, hasMatches: boolean) => (!!q && hasMatches) || !collapsed[id]

  // Build groups with pre-assigned flat indices (drives keyboard nav)
  const { groups, totalItems } = useMemo(() => {
    const groups: PaletteGroup[] = []
    let flatIdx = 0

    // Base group
    const filteredBuiltinNodes = BUILTIN_NODES.filter((n) => !q || t(n.labelKey).toLowerCase().includes(q) || t(n.descriptionKey).toLowerCase().includes(q))
    const filteredBuiltinExts  = allNodePacks.filter((e) => e.builtin && (!q || e.name.toLowerCase().includes(q) || (e.description ?? '').toLowerCase().includes(q)))
    const baseCount   = filteredBuiltinNodes.length + filteredBuiltinExts.length
    const baseVisible = !q || baseCount > 0
    const baseExp     = isExpanded('base', baseCount > 0)

    if (baseVisible) {
      const items: PaletteGroup['items'] = []
      if (baseExp) {
        filteredBuiltinNodes.forEach((n) => items.push({ kind: 'node', data: n, flatIdx: flatIdx++ }))
        filteredBuiltinExts.forEach((e)  => items.push({ kind: 'ext',  data: e, flatIdx: flatIdx++ }))
      }
      groups.push({ id: 'base', title: t('workflows.base'), expanded: baseExp, items })
    }

    // Non-builtin groups
    for (const [extId, { nodePackName, nodePackAuthor, nodes }] of nonBuiltinMap) {
      const filtered     = nodes.filter((e) => !q || e.name.toLowerCase().includes(q) || (e.description ?? '').toLowerCase().includes(q))
      if (q && filtered.length === 0) continue
      const displayNodes = q ? filtered : nodes
      const expanded     = isExpanded(extId, filtered.length > 0)
      const items: PaletteGroup['items'] = []
      if (expanded) displayNodes.forEach((e) => items.push({ kind: 'ext', data: e, flatIdx: flatIdx++ }))
      groups.push({ id: extId, title: nodePackName, author: nodePackAuthor || undefined, expanded, items })
    }

    return { groups, totalItems: flatIdx }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- isExpanded only reads `collapsed`, already a dep
  }, [q, allNodePacks, nonBuiltinMap, collapsed, t])

  useEffect(() => { setActiveIndex(0) }, [query])
  useEffect(() => { inputRef.current?.focus() }, [])

  // Flat list for Enter key (derived from groups)
  const flatItems = useMemo(() => groups.flatMap((g) => g.items), [groups])

  const handleKey = useCallback((e: React.KeyboardEvent) => {
    if (e.key === 'Escape') { onClose(); return }
    if (e.key === 'ArrowDown') { e.preventDefault(); setActiveIndex((i) => Math.min(i + 1, totalItems - 1)); return }
    if (e.key === 'ArrowUp')   { e.preventDefault(); setActiveIndex((i) => Math.max(i - 1, 0)); return }
    if (e.key === 'Enter') {
      e.preventDefault()
      const item = flatItems[activeIndex]
      if (!item) return
      if (item.kind === 'node') onSelect(item.data.type)
      else onSelect('nodePackNode', item.data.id)
    }
  }, [activeIndex, flatItems, totalItems, onSelect, onClose])

  return (
    <Dialog open onOpenChange={(open) => { if (!open) onClose() }}>
      <DialogContent
        className="max-w-md overflow-hidden p-0"
        onOpenAutoFocus={(event) => {
          event.preventDefault()
          inputRef.current?.focus()
        }}
      >
        <DialogHeader className="sr-only">
          <DialogTitle>{t('workflows.addNode')}</DialogTitle>
          <DialogDescription>{t('workflows.addNodeDescription')}</DialogDescription>
        </DialogHeader>
        {/* Search input */}
        <div className="flex items-center gap-3 border-b border-border px-4 py-3 pr-12">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="shrink-0 text-muted-foreground">
            <circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/>
          </svg>
          <input
            ref={inputRef}
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={handleKey}
            placeholder={t('workflows.search')}
            className="flex-1 bg-transparent text-sm text-foreground placeholder:text-muted-foreground focus:outline-none"
          />
          <kbd className="rounded border border-border bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">Esc</kbd>
        </div>

        {/* Groups */}
        <div className="max-h-96 overflow-y-auto py-1.5">
          {groups.map((group) => (
            <div key={group.id}>

              {/* Group header */}
              <button
                onClick={() => toggleGroup(group.id)}
                className="group flex w-full items-center gap-2 px-4 py-2 transition-colors hover:bg-muted/50"
              >
                <svg
                  width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"
                  className="shrink-0 text-muted-foreground transition-colors group-hover:text-foreground"
                  style={{ transform: group.expanded ? 'rotate(90deg)' : 'rotate(0deg)', transition: 'transform 0.15s ease' }}
                >
                  <polyline points="9 18 15 12 9 6"/>
                </svg>
                <div className="flex items-baseline gap-2 min-w-0">
                  <span className="text-[11px] font-semibold text-muted-foreground transition-colors group-hover:text-foreground">{group.title}</span>
                  {group.author && <span className="truncate text-[10px] text-muted-foreground">{group.author}</span>}
                </div>
                <span className="ml-auto shrink-0 text-[10px] text-muted-foreground">{group.items.length}</span>
              </button>

              {/* Group items */}
              {group.expanded && group.items.map((item) => {
                const isActive = activeIndex === item.flatIdx
                if (item.kind === 'node') {
                  const n = item.data
                  return (
                    <button
                      key={n.type}
                      onMouseEnter={() => setActiveIndex(item.flatIdx)}
                      onClick={() => onSelect(n.type)}
                      className={`flex w-full items-center gap-3 px-4 py-3 pl-9 text-left transition-colors ${isActive ? 'bg-muted' : 'hover:bg-muted/50'}`}
                    >
                      <span className="w-1.5 h-1.5 rounded-full shrink-0" style={{ background: n.color }} />
                      <span className="text-sm text-foreground">{t(n.labelKey)}</span>
                      <span className="ml-auto text-xs text-muted-foreground">{t(n.descriptionKey)}</span>
                    </button>
                  )
                }
                const e = item.data
                return (
                  <button
                    key={e.id}
                    onMouseEnter={() => setActiveIndex(item.flatIdx)}
                    onClick={() => onSelect('nodePackNode', e.id)}
                    className={`flex w-full items-center gap-3 px-4 py-3 pl-9 text-left transition-colors ${isActive ? 'bg-muted' : 'hover:bg-muted/50'}`}
                  >
                    <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-primary" />
                    <span className="text-sm text-foreground">{e.name}</span>
                    <div className="flex items-center gap-1 ml-auto shrink-0">
                      <NodePackInputBadges nodePack={e} />
                      <svg width="8" height="8" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="text-muted-foreground">
                        <path d="M5 12h14M13 6l6 6-6 6"/>
                      </svg>
                      <span className="text-[10px] text-muted-foreground">{e.output}</span>
                    </div>
                  </button>
                )
              })}

            </div>
          ))}

          {totalItems === 0 && groups.length === 0 && (
            <p className="px-4 py-6 text-center text-sm text-muted-foreground">{t('workflows.noResultsFor', { query })}</p>
          )}
        </div>
      </DialogContent>
    </Dialog>
  )
}

// ─── Help modal ───────────────────────────────────────────────────────────────

function HelpModal({ onClose }: { onClose: () => void }) {
  const { t } = useI18n()
  return (
    <Dialog open onOpenChange={(open) => { if (!open) onClose() }}>
      <DialogContent className="flex max-h-[80vh] w-[520px] max-w-[92vw] flex-col overflow-hidden p-0">

        {/* Header */}
        <DialogHeader className="sticky top-0 z-10 border-b border-border bg-card px-5 py-4 pr-12">
          <div className="flex items-center gap-2.5">
            <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg border border-primary/20 bg-primary/10">
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="text-primary">
                <circle cx="12" cy="12" r="10"/><path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3"/><line x1="12" y1="17" x2="12.01" y2="17"/>
              </svg>
            </div>
            <DialogTitle className="text-sm font-semibold text-foreground">{t('workflows.helpTitle')}</DialogTitle>
          </div>
          <DialogDescription className="sr-only">{t('workflows.helpDescription')}</DialogDescription>
        </DialogHeader>

        <div className="flex flex-col gap-5 overflow-y-auto px-5 py-5">

          {/* Concept */}
          <section className="flex flex-col gap-2">
            <h3 className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">{t('workflows.concept')}</h3>
            <p className="text-[12px] leading-relaxed text-muted-foreground">{t('workflows.conceptBody')}</p>
          </section>


          {/* Node types */}
          <section className="flex flex-col gap-2.5">
            <h3 className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">{t('workflows.nodeTypes')}</h3>
            <div className="flex flex-col gap-2">

              <div className="flex items-start gap-3 rounded-lg border border-border bg-muted/50 p-3">
                <span className="inline-flex items-center px-1.5 py-0.5 rounded text-[9px] font-medium border border-sky-500/30 bg-sky-500/10 text-sky-400 shrink-0 mt-0.5">{t('workflows.typeImage')}</span>
                <div>
                  <p className="text-[11px] font-medium text-foreground">{t('workflows.nodeImage')}</p>
                  <p className="mt-0.5 text-[11px] leading-relaxed text-muted-foreground">{t('workflows.sourceImage')}</p>
                </div>
              </div>

              <div className="flex items-start gap-3 rounded-lg border border-border bg-muted/50 p-3">
                <span className="inline-flex items-center px-1.5 py-0.5 rounded text-[9px] font-medium border border-amber-500/30 bg-amber-500/10 text-amber-400 shrink-0 mt-0.5">{t('workflows.typeText')}</span>
                <div>
                  <p className="text-[11px] font-medium text-foreground">{t('workflows.nodeText')}</p>
                  <p className="mt-0.5 text-[11px] leading-relaxed text-muted-foreground">{t('workflows.sourceText')}</p>
                </div>
              </div>

              <div className="flex items-start gap-3 rounded-lg border border-border bg-muted/50 p-3">
                <span className="mt-0.5 inline-flex shrink-0 items-center rounded border border-primary/30 bg-primary/10 px-1.5 py-0.5 text-[9px] font-medium text-primary">{t('workflows.typeMesh')}</span>
                <div>
                  <p className="text-[11px] font-medium text-foreground">{t('workflows.nodeMesh')}</p>
                  <p className="mt-0.5 text-[11px] leading-relaxed text-muted-foreground">{t('workflows.sourceMesh')}</p>
                </div>
              </div>

              <div className="flex items-start gap-3 rounded-lg border border-border bg-muted/50 p-3">
                <div className="flex gap-1 shrink-0 mt-0.5">
                  <span className="inline-flex items-center px-1.5 py-0.5 rounded text-[9px] font-medium border border-sky-500/30 bg-sky-500/10 text-sky-400">{t('workflows.typeImage')}</span>
                  <span className="flex items-center text-[9px] text-muted-foreground">→</span>
                  <span className="inline-flex items-center rounded border border-primary/30 bg-primary/10 px-1.5 py-0.5 text-[9px] font-medium text-primary">{t('workflows.typeMesh')}</span>
                </div>
                <div>
                  <p className="text-[11px] font-medium text-foreground">{t('workflows.modelPack')} <span className="text-[10px] font-normal text-muted-foreground">({t('workflows.aiGenerator')})</span></p>
                  <p className="mt-0.5 text-[11px] leading-relaxed text-muted-foreground">{t('workflows.modelPackDescription')}</p>
                </div>
              </div>

              <div className="flex items-start gap-3 rounded-lg border border-border bg-muted/50 p-3">
                <div className="flex gap-1 shrink-0 mt-0.5">
                  <span className="inline-flex items-center rounded border border-primary/30 bg-primary/10 px-1.5 py-0.5 text-[9px] font-medium text-primary">{t('workflows.typeMesh')}</span>
                  <span className="flex items-center text-[9px] text-muted-foreground">→</span>
                  <span className="inline-flex items-center rounded border border-primary/30 bg-primary/10 px-1.5 py-0.5 text-[9px] font-medium text-primary">{t('workflows.typeMesh')}</span>
                </div>
                <div>
                  <p className="text-[11px] font-medium text-foreground">{t('workflows.processPack')} <span className="text-[10px] font-normal text-muted-foreground">({t('workflows.meshProcessor')})</span></p>
                  <p className="mt-0.5 text-[11px] leading-relaxed text-muted-foreground">{t('workflows.processPackDescription')}</p>
                </div>
              </div>

              <div className="flex items-start gap-3 rounded-lg border border-border bg-muted/50 p-3">
                <span className="mt-0.5 inline-flex shrink-0 items-center rounded border border-sky-500/30 bg-sky-500/10 px-1.5 py-0.5 text-[9px] font-medium text-sky-400">{t('workflows.typeScene')}</span>
                <div>
                  <p className="text-[11px] font-medium text-foreground">{t('workflows.nodeOutput')}</p>
                  <p className="mt-0.5 text-[11px] leading-relaxed text-muted-foreground">{t('workflows.outputDescription')}</p>
                </div>
              </div>

            </div>
          </section>

          {/* Tips */}
          <section className="flex flex-col gap-2">
            <h3 className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">{t('workflows.tips')}</h3>
            <ul className="flex flex-col gap-1.5">
              {[
                ['Space', t('workflows.tipSpace')],
                ['Eye icon', t('workflows.tipEye')],
                ['Drag handle → canvas', t('workflows.tipDrag')],
                ['Right-click a link', t('workflows.tipRightClick')],
                [t('workflows.run'), t('workflows.tipRun')],
              ].map(([key, desc]) => (
                <li key={key} className="flex items-start gap-2 text-[11px] text-muted-foreground">
                  <span className="mt-px shrink-0 rounded border border-border bg-muted px-1.5 py-px text-[10px] font-medium text-foreground">{key}</span>
                  <span>{desc}</span>
                </li>
              ))}
            </ul>
          </section>

        </div>
      </DialogContent>
    </Dialog>
  )
}

// ─── Connection type helpers ──────────────────────────────────────────────────

function getNodeOutputType(node: Node | undefined, allExts: WorkflowNodePack[]): string | undefined {
  if (!node) return undefined
  if (node.type === 'imageNode') return 'image'
  if (node.type === 'meshNode')  return 'mesh'
  if (node.type === 'textNode')  return 'text'
  return allExts.find((e) => e.id === (node.data as WFNodeData)?.nodePackId)?.output
}

function getNodeInputType(
  node: Node | undefined,
  targetHandle: string | null | undefined,
  allExts: WorkflowNodePack[],
): string | undefined {
  if (!node) return undefined
  if (node.type === 'outputNode')  return 'mesh'
  if (node.type === 'previewNode') return 'image'
  const ext = allExts.find((e) => e.id === (node.data as WFNodeData)?.nodePackId)
  if (ext?.inputs && ext.inputs.length > 1 && targetHandle) {
    const idx = parseInt(targetHandle.replace('input-', ''), 10)
    return ext.inputs[isNaN(idx) ? 0 : idx] ?? ext.input
  }
  return ext?.input
}

// ─── Workflow canvas (inner, requires ReactFlowProvider) ──────────────────────

function WorkflowCanvasInner({
  workflow, allNodePacks, onSave, panelOpen, onTogglePanel, onOpen, onImport,
}: {
  workflow:         Workflow
  allNodePacks:    WorkflowNodePack[]
  onSave:           (w: Workflow) => void
  panelOpen:        boolean
  onTogglePanel:    () => void
  onOpen:           () => void
  onImport:         () => void
}) {
  const { screenToFlowPosition, getNode } = useReactFlow()
  const { t } = useI18n()
  const { runState, run: runWorkflow, cancel, reset: resetRun } = useWorkflowRunStore()
  const selectedImageData = useAppStore((s) => s.selectedImageData)
  const showToast = useAppStore((s) => s.showToast)
  const isRunning = runState.status === 'running'

  const [nodes, setNodes, onNodesChange] = useNodesState(workflow.nodes as Node[])
  const [edges, setEdges, onEdgesChange] = useEdgesState(workflow.edges as Edge[])
  const [paletteOpen, setPaletteOpen] = useState(false)
  const [helpOpen, setHelpOpen] = useState(false)

  // Pending connection: set when user drags a handle and releases on empty canvas
  const pendingConnectionRef  = useRef<OnConnectStartParams | null>(null)
  const connectionCompletedRef = useRef(false)
  const [pendingDropPos, setPendingDropPos] = useState<{ x: number; y: number } | null>(null)

  const saveTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const preflightToastTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const didMountRef = useRef(false)

  // ─── Undo / Redo ──────────────────────────────────────────────────────────
  type Snapshot = { nodes: Node[]; edges: Edge[] }
  const historyRef  = useRef<Snapshot[]>([{ nodes: workflow.nodes as Node[], edges: workflow.edges as Edge[] }])
  const histIdxRef  = useRef(0)
  const [histIdx, setHistIdx] = useState(0)
  const skipPushRef = useRef(true) // skip the initial autosave-triggered push

  // Re-sync when workflow switches
  useEffect(() => {
    setNodes(workflow.nodes as Node[])
    setEdges(workflow.edges as Edge[])
    historyRef.current = [{ nodes: workflow.nodes as Node[], edges: workflow.edges as Edge[] }]
    histIdxRef.current = 0
    setHistIdx(0)
    skipPushRef.current = true
    // eslint-disable-next-line react-hooks/exhaustive-deps -- re-sync only when the workflow switches; adding nodes/edges would reset the editor on every change
  }, [workflow.id])

  // Auto-save + history push debounced
  useEffect(() => {
    if (saveTimer.current) clearTimeout(saveTimer.current)
    saveTimer.current = setTimeout(() => {
      const updated: Workflow = {
        ...workflow,
        nodes: nodes as WFNode[],
        edges: edges as WFEdge[],
        updatedAt: new Date().toISOString(),
      }
      onSave(updated)

      if (!skipPushRef.current) {
        const next = historyRef.current.slice(0, histIdxRef.current + 1)
        next.push({ nodes, edges })
        if (next.length > 50) next.shift()
        historyRef.current = next
        const newIdx = next.length - 1
        histIdxRef.current = newIdx
        setHistIdx(newIdx)
      }
      skipPushRef.current = false
    }, 500)
    return () => { if (saveTimer.current) clearTimeout(saveTimer.current) }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- debounce on editable state; latest workflow/onSave read in the timeout
  }, [nodes, edges])

  const preflightIssues = useMemo(() => {
    const draft: Workflow = {
      ...workflow,
      nodes: nodes as WFNode[],
      edges: edges as WFEdge[],
      updatedAt: workflow.updatedAt,
    }
    return validateWorkflowPreflight(draft, allNodePacks, {
      selectedImageData,
      executionMode: 'web',
    })
  }, [workflow, nodes, edges, allNodePacks, selectedImageData])

  useEffect(() => {
    if (!didMountRef.current) {
      didMountRef.current = true
      return
    }
    if (preflightToastTimer.current) clearTimeout(preflightToastTimer.current)
    if (preflightIssues.length === 0) return
    preflightToastTimer.current = setTimeout(() => {
      showToast(preflightIssues[0].message)
    }, 250)
    return () => {
      if (preflightToastTimer.current) clearTimeout(preflightToastTimer.current)
    }
  }, [preflightIssues, showToast])

  const undo = useCallback(() => {
    const idx = histIdxRef.current
    if (idx <= 0) return
    const newIdx = idx - 1
    const snap = historyRef.current[newIdx]
    skipPushRef.current = true
    setNodes(snap.nodes)
    setEdges(snap.edges)
    histIdxRef.current = newIdx
    setHistIdx(newIdx)
  }, [setNodes, setEdges])

  const redo = useCallback(() => {
    const idx = histIdxRef.current
    if (idx >= historyRef.current.length - 1) return
    const newIdx = idx + 1
    const snap = historyRef.current[newIdx]
    skipPushRef.current = true
    setNodes(snap.nodes)
    setEdges(snap.edges)
    histIdxRef.current = newIdx
    setHistIdx(newIdx)
  }, [setNodes, setEdges])

  const canUndo = histIdx > 0
  const canRedo = histIdx < historyRef.current.length - 1

  const isValidConnection = useCallback((connection: Edge | Connection) => {
    const srcType = getNodeOutputType(getNode(connection.source) as Node, allNodePacks)
    const tgtType = getNodeInputType(getNode(connection.target) as Node, connection.targetHandle, allNodePacks)
    if (srcType && tgtType && srcType !== tgtType) return false  // type mismatch (unknown types allowed)
    // Reject connections that would create a cycle: if the target can already
    // reach the source, adding source→target closes a loop.
    if (connection.source && connection.target) {
      const stack = [connection.target]
      const seen  = new Set<string>()
      while (stack.length > 0) {
        const id = stack.pop()!
        if (id === connection.source) return false
        if (seen.has(id)) continue
        seen.add(id)
        for (const e of edges) if (e.source === id) stack.push(e.target)
      }
    }
    return true
  }, [getNode, allNodePacks, edges])

  const onConnectStart = useCallback((_: MouseEvent | TouchEvent, params: OnConnectStartParams) => {
    pendingConnectionRef.current  = params
    connectionCompletedRef.current = false
  }, [])

  const onConnect = useCallback((params: Connection) => {
    connectionCompletedRef.current = true
    setEdges((eds) => addEdge({ ...params, ...DEFAULT_EDGE_OPTS }, eds))
  }, [setEdges])

  const onConnectEnd = useCallback((event: MouseEvent | TouchEvent) => {
    if (connectionCompletedRef.current || !pendingConnectionRef.current?.nodeId) {
      pendingConnectionRef.current = null
      return
    }
    // Dropped on empty canvas opens the palette; a real node or handle closes it.
    const target = event.target as Element
    const nodeEl = target.closest('.react-flow__node')
    if (target.closest('.react-flow__handle') || nodeEl) {
      pendingConnectionRef.current = null
      return
    }
    const clientX = 'clientX' in event ? event.clientX : (event as TouchEvent).changedTouches[0].clientX
    const clientY = 'clientY' in event ? event.clientY : (event as TouchEvent).changedTouches[0].clientY
    setPendingDropPos({ x: clientX, y: clientY })
    setPaletteOpen(true)
  }, [])

  const onDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault(); e.dataTransfer.dropEffect = 'copy'
  }, [])

  const onDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    const position = screenToFlowPosition({ x: e.clientX, y: e.clientY })

    const nodeType = readDragData(e.dataTransfer, DRAG_NODE_KEY)
    if (nodeType) {
      setNodes((nds) => [
        ...nds,
        { id: newId(), type: nodeType, position, data: { enabled: true, params: {} } as WFNodeData },
      ])
      return
    }

    const nodePackId = readDragData(e.dataTransfer, DRAG_KEY)
    if (!nodePackId) return
    setNodes((nds) => [
      ...nds,
      { id: newId(), type: 'nodePackNode', position, data: { nodePackId, enabled: true, params: {} } as WFNodeData },
    ])
  }, [screenToFlowPosition, setNodes])

  // Keyboard shortcuts (Space, Ctrl+Z, Ctrl+Y / Ctrl+Shift+Z)
  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement).tagName
      if (tag === 'INPUT' || tag === 'TEXTAREA') return
      if (e.code === 'Space') {
        e.preventDefault()
        setPaletteOpen(true)
        return
      }
      if ((e.ctrlKey || e.metaKey) && !e.shiftKey && e.key === 'z') {
        e.preventDefault()
        undo()
        return
      }
      if ((e.ctrlKey || e.metaKey) && (e.key === 'y' || (e.shiftKey && e.key === 'z'))) {
        e.preventDefault()
        redo()
      }
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [undo, redo])

  // Copy/paste selected nodes (Ctrl+C / Ctrl+V) — works across workflow tabs
  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement).tagName
      if (tag === 'INPUT' || tag === 'TEXTAREA') return
      if (!(e.ctrlKey || e.metaKey) || e.shiftKey) return

      if (e.key === 'c') {
        const selected = nodes.filter((n) => n.selected)
        if (selected.length === 0) return
        const selIds = new Set(selected.map((n) => n.id))
        const copied = selected.map((n) => {
          // Child copied without its container → detach to absolute coordinates
          if (n.parentId && !selIds.has(n.parentId)) {
            const parent = nodes.find((p) => p.id === n.parentId)
            return {
              ...structuredClone(n),
              parentId: undefined,
              position: { x: n.position.x + (parent?.position.x ?? 0), y: n.position.y + (parent?.position.y ?? 0) },
            }
          }
          return structuredClone(n)
        })
        const copiedEdges = edges
          .filter((ed) => selIds.has(ed.source) && selIds.has(ed.target))
          .map((ed) => structuredClone(ed))
        _nodeClipboard.current = { nodes: copied, edges: copiedEdges, pastes: 0 }
        return
      }

      if (e.key === 'v') {
        const clip = _nodeClipboard.current
        if (!clip || clip.nodes.length === 0) return
        clip.pastes += 1
        const offset = 32 * clip.pastes
        const idMap = new Map(clip.nodes.map((n) => [n.id, newId()]))
        const pasted: Node[] = clip.nodes.map((n) => {
          const keepParent = n.parentId != null && idMap.has(n.parentId)
          return {
            ...structuredClone(n),
            id:       idMap.get(n.id)!,
            parentId: keepParent ? idMap.get(n.parentId!) : undefined,
            // Children keep their parent-relative position; top-level nodes shift
            // a bit more on every paste so repeated pastes don't stack.
            position: keepParent ? n.position : { x: n.position.x + offset, y: n.position.y + offset },
            selected: true,
          }
        })
        const pastedEdges: Edge[] = clip.edges.map((ed) => ({
          ...structuredClone(ed),
          id:     `e-${newId()}`,
          source: idMap.get(ed.source)!,
          target: idMap.get(ed.target)!,
        }))
        setNodes((nds) => [...nds.map((n) => ({ ...n, selected: false })), ...pasted])
        setEdges((eds) => [...eds, ...pastedEdges])
      }
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [nodes, edges, setNodes, setEdges])

  const addNodeFromPalette = useCallback((type: string, nodePackId?: string) => {
    const position = screenToFlowPosition(
      pendingDropPos ?? { x: window.innerWidth / 2, y: window.innerHeight / 2 }
    )
    const newNodeId = newId()
    setNodes((nds) => {
      const node: Node = {
        id: newNodeId, type,
        position,
        data: { nodePackId, enabled: true, params: {} } as WFNodeData,
      }
      return [...nds, node]
    })

    // If palette was opened from a connection drag, wire the edge automatically.
    // NodePackNodes use id'd handles (input-0 / output), not the default null
    // handle, so the new node's side must reference them or React Flow can't place
    // the edge ("Couldn't create edge for target handle id: null").
    const pending = pendingConnectionRef.current
    if (pending?.nodeId) {
      const isSource = pending.handleType === 'source'
      const isExt = type === 'nodePackNode'
      // Skip wiring when the new node can't take the connection: a source-only node
      // (Image/Text/Mesh) as target, or a sink-only node (Output/Preview) as
      // source — those have no matching handle and would orphan the edge.
      const canWire = isSource ? !NODE_TYPES_WITHOUT_TARGET.has(type) : !NODE_TYPES_WITHOUT_SOURCE.has(type)
      if (canWire) {
        const edge = isSource
          ? { id: newId(), source: pending.nodeId, sourceHandle: pending.handleId ?? undefined, target: newNodeId, targetHandle: isExt ? 'input-0' : undefined }
          : { id: newId(), source: newNodeId, sourceHandle: isExt ? 'output' : undefined, target: pending.nodeId, targetHandle: pending.handleId ?? undefined }
        setEdges((eds) => addEdge({ ...edge, ...DEFAULT_EDGE_OPTS }, eds))
      }
    }

    pendingConnectionRef.current = null
    setPendingDropPos(null)
    setPaletteOpen(false)
  }, [screenToFlowPosition, setNodes, setEdges, pendingDropPos])

  const handleRun = useCallback(() => {
    if (isRunning) { cancel(); return }
    if (preflightIssues.length > 0) {
      showToast(preflightIssues[0].message)
      return
    }
    const wf: Workflow = { ...workflow, nodes: nodes as WFNode[], edges: edges as WFEdge[], updatedAt: new Date().toISOString() }
    onSave(wf)
    runWorkflow(wf, allNodePacks)
  }, [workflow, nodes, edges, onSave, allNodePacks, isRunning, runWorkflow, cancel, preflightIssues, showToast])

  return (
    <div className="flex flex-col flex-1 overflow-hidden">

      {paletteOpen && (
        <NodePalette
          allNodePacks={allNodePacks}
          onSelect={addNodeFromPalette}
          onClose={() => {
            pendingConnectionRef.current = null
            setPendingDropPos(null)
            setPaletteOpen(false)
          }}
        />
      )}

      {/* Header toolbar */}
      <div className="flex shrink-0 items-center gap-2 border-b border-border/45 bg-card/65 px-3 py-2">

        {/* Open */}
        <button
          onClick={onOpen}
          title={t('workflows.open')}
          className="flex shrink-0 items-center gap-2 rounded-md border border-input bg-background px-3 py-1.5 text-muted-foreground transition-colors hover:border-ring hover:bg-muted hover:text-foreground"
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/>
          </svg>
          <span className="text-sm font-medium">{t('workflows.open')}</span>
        </button>

        {/* Import */}
        <button
          onClick={onImport}
          title={t('workflows.import')}
          className="flex shrink-0 items-center gap-2 rounded-md border border-input bg-background px-3 py-1.5 text-muted-foreground transition-colors hover:border-ring hover:bg-muted hover:text-foreground"
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
            <polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/>
          </svg>
          <span className="text-sm font-medium">{t('workflows.import')}</span>
        </button>

        <div className="mx-0.5 h-6 w-px shrink-0 bg-border" />

        {/* Undo */}
        <button
          onClick={undo}
          disabled={!canUndo}
          title={t('workflows.undo')}
          className="rounded-md border border-input bg-background p-2 text-muted-foreground transition-colors hover:border-ring hover:bg-muted hover:text-foreground disabled:cursor-not-allowed disabled:opacity-30"
        >
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M3 7v6h6"/><path d="M3 13A9 9 0 1 0 5.7 6.3"/>
          </svg>
        </button>

        {/* Redo */}
        <button
          onClick={redo}
          disabled={!canRedo}
          title={t('workflows.redo')}
          className="rounded-md border border-input bg-background p-2 text-muted-foreground transition-colors hover:border-ring hover:bg-muted hover:text-foreground disabled:cursor-not-allowed disabled:opacity-30"
        >
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M21 7v6h-6"/><path d="M21 13A9 9 0 1 1 18.3 6.3"/>
          </svg>
        </button>

        <div className="flex-1" />

        <div className="flex items-center gap-1">
          {/* Run / Stop */}
          <button
            onClick={handleRun}
            className={`flex items-center gap-2 rounded-md border px-3.5 py-1.5 transition-colors
              ${isRunning
                ? 'border-destructive/30 bg-destructive/10 text-destructive hover:border-destructive/50 hover:bg-destructive/20'
                : 'border-primary/30 bg-primary/10 text-primary hover:border-primary/50 hover:bg-primary/20'}`}
          >
            {isRunning ? (
              <>
                <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="6" width="12" height="12" rx="1"/></svg>
                <span className="text-sm font-semibold">{t('workflows.stop')}</span>
              </>
            ) : (
              <>
                <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><polygon points="5 3 19 12 5 21 5 3"/></svg>
                <span className="text-sm font-semibold">{t('workflows.run')}</span>
              </>
            )}
          </button>

          {/* Progress indicator */}
          {isRunning && (
            <div className="flex max-w-[180px] items-center gap-1.5 rounded-md border border-border bg-muted px-2.5 py-1.5">
              <svg className="animate-spin shrink-0 text-primary" width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
                <path d="M21 12a9 9 0 1 1-6.219-8.56"/>
              </svg>
              <span className="truncate text-[11px] text-muted-foreground">{runState.blockStep}</span>
            </div>
          )}

          {/* Help */}
          <button
            onClick={() => setHelpOpen(true)}
            title={t('workflows.help')}
            className="flex h-8 w-8 items-center justify-center rounded-md border border-input bg-background p-2 text-sm font-semibold text-muted-foreground transition-colors hover:border-ring hover:bg-muted hover:text-foreground"
          >
            ?
          </button>
        </div>
      </div>

      {helpOpen && <HelpModal onClose={() => setHelpOpen(false)} />}

      {/* Run error banner — surface subprocess/backend failures instead of failing silently */}
      {runState.status === 'error' && runState.error && (
        <div className="flex shrink-0 items-start gap-2.5 border-b border-destructive/30 bg-destructive/10 px-3 py-2.5">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="mt-0.5 shrink-0 text-destructive">
            <circle cx="12" cy="12" r="10" /><line x1="12" y1="8" x2="12" y2="12" /><line x1="12" y1="16" x2="12.01" y2="16" />
          </svg>
          <div className="min-w-0 flex-1">
            <p className="text-[11px] font-semibold text-destructive">{t('workflows.runFailed')}</p>
            <pre className="mt-1 max-h-28 select-text overflow-y-auto whitespace-pre-wrap break-words font-mono text-[10.5px] leading-relaxed text-destructive/85">{runState.error}</pre>
          </div>
          <button
            onClick={resetRun}
            className="shrink-0 rounded-md border border-border px-2.5 py-1 text-[11px] font-medium text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
          >
            {t('common.close')}
          </button>
        </div>
      )}

      {/* React Flow canvas */}
      <div className="flex-1 relative" onDragOver={onDragOver} onDrop={onDrop}>

        {/* No model node warning */}
        {!nodes.some((n) => n.type === 'nodePackNode' && allNodePacks.find((e) => e.id === (n.data as WFNodeData).nodePackId && e.type === 'model')) && (
          <div className="absolute top-3 left-1/2 -translate-x-1/2 z-10 pointer-events-none">
            <div className="flex items-center gap-1.5 whitespace-nowrap rounded-md border border-primary/20 bg-primary/10 px-2.5 py-1 text-primary">
              <svg width="11" height="11" viewBox="0 0 24 24" fill="currentColor" className="shrink-0">
                <path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm1 15h-2v-6h2v6zm0-8h-2V7h2v2z"/>
              </svg>
              <span className="text-[10px] font-medium">{t('workflows.noModelNode')}</span>
            </div>
          </div>
        )}

        {/* Floating panel toggle — over the canvas, below the header */}
        <button
          onClick={onTogglePanel}
          title={panelOpen ? t('workflows.closeNodeLibrary') : t('workflows.openNodeLibrary')}
          className="absolute left-3 top-3 z-10 rounded-md border border-border bg-card/95 p-2 text-muted-foreground shadow-md transition-colors backdrop-blur-sm hover:border-ring hover:bg-muted hover:text-foreground"
        >
          <PanelToggleIcon open={panelOpen} />
        </button>

        <ReactFlow
          nodes={nodes}
          edges={edges}
          nodeTypes={NODE_TYPES}
          edgeTypes={EDGE_TYPES}
          onNodesChange={onNodesChange}
          onEdgesChange={onEdgesChange}
          onConnectStart={onConnectStart}
          onConnect={onConnect}
          isValidConnection={isValidConnection}
          onConnectEnd={onConnectEnd}
          onEdgeContextMenu={(e, edge) => { e.preventDefault(); setEdges((eds) => eds.filter((ed) => ed.id !== edge.id)) }}
          defaultEdgeOptions={DEFAULT_EDGE_OPTS}
          deleteKeyCode="Delete"
          connectionLineStyle={{ stroke: '#71717a', strokeWidth: 1.5 }}
          fitView
          fitViewOptions={{ padding: 0.3 }}
          proOptions={{ hideAttribution: true }}
          className="bg-[#191919]"
        >
          <Background color="#303030" gap={24} size={1} />
        </ReactFlow>
      </div>
    </div>
  )
}

// ─── Mini graph preview ───────────────────────────────────────────────────────
// Schematic thumbnail of a workflow's graph for the Open popup cards — plain
// SVG built from stored node positions, no React Flow instance needed.

// Node tint by role, echoing the real canvas: inputs blue, processing blue,
// outputs cyan-blue, everything else neutral.
const MINI_NODE_TINTS: Record<string, { fill: string; stroke: string }> = {
  imageNode:     { fill: 'rgba(86,128,184,0.22)',  stroke: '#5680b8' },
  textNode:      { fill: 'rgba(86,128,184,0.22)',  stroke: '#5680b8' },
  meshNode:      { fill: 'rgba(86,128,184,0.22)',  stroke: '#5680b8' },
  nodePackNode: { fill: 'rgba(93,148,217,0.22)', stroke: '#5d94d9' },
  outputNode:    { fill: 'rgba(86,128,184,0.22)',  stroke: '#5680b8' },
  previewNode:   { fill: 'rgba(86,128,184,0.22)',  stroke: '#5680b8' },
}
const MINI_NODE_DEFAULT_TINT = { fill: 'rgba(113,113,122,0.25)', stroke: '#71717a' }

function WorkflowMiniPreview({ wf }: { wf: Workflow }): JSX.Element {
  const VIEW_W = 200
  const VIEW_H = 88
  const PAD = 12

  if (wf.nodes.length === 0) {
    return (
      <div className="flex h-full w-full items-center justify-center text-muted-foreground/70">
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
          <rect x="3" y="3" width="7" height="5" rx="1"/><rect x="14" y="16" width="7" height="5" rx="1"/>
          <path d="M10 5.5h5a2 2 0 0 1 2 2V16"/>
        </svg>
      </div>
    )
  }

  // Children of a container store positions relative to their parent
  const byId  = new Map(wf.nodes.map((n) => [n.id, n]))
  const boxes = wf.nodes.map((n) => {
    const parent = n.parentId ? byId.get(n.parentId) : undefined
    return {
      id:   n.id,
      type: n.type,
      x:    n.position.x + (parent?.position.x ?? 0),
      y:    n.position.y + (parent?.position.y ?? 0),
      w:    n.width ?? (n.style?.width  as number | undefined) ?? 150,
      h:    n.height ?? (n.style?.height as number | undefined) ?? 48,
    }
  })
  const boxById = new Map(boxes.map((b) => [b.id, b]))

  const minX  = Math.min(...boxes.map((b) => b.x))
  const minY  = Math.min(...boxes.map((b) => b.y))
  const maxX  = Math.max(...boxes.map((b) => b.x + b.w))
  const maxY  = Math.max(...boxes.map((b) => b.y + b.h))
  // Cap the scale so a near-empty graph doesn't blow one node up to card size
  const scale = Math.min(
    (VIEW_W - PAD * 2) / Math.max(maxX - minX, 1),
    (VIEW_H - PAD * 2) / Math.max(maxY - minY, 1),
    0.5,
  )
  const offX = (VIEW_W - (maxX - minX) * scale) / 2
  const offY = (VIEW_H - (maxY - minY) * scale) / 2
  const tx   = (x: number): number => offX + (x - minX) * scale
  const ty   = (y: number): number => offY + (y - minY) * scale

  return (
    <svg viewBox={`0 0 ${VIEW_W} ${VIEW_H}`} className="w-full h-full" preserveAspectRatio="xMidYMid meet">
      <defs>
        <pattern id="wf-mini-grid" width="11" height="11" patternUnits="userSpaceOnUse">
          <circle cx="1" cy="1" r="0.8" fill="#303030" />
        </pattern>
      </defs>
      <rect width={VIEW_W} height={VIEW_H} fill="url(#wf-mini-grid)" />
      {wf.edges.map((e) => {
        const s = boxById.get(e.source)
        const t = boxById.get(e.target)
        if (!s || !t) return null
        const x1 = tx(s.x + s.w), y1 = ty(s.y + s.h / 2)
        const x2 = tx(t.x),       y2 = ty(t.y + t.h / 2)
        const d  = Math.max(Math.abs(x2 - x1) * 0.45, 6)
        return (
          <path
            key={e.id}
            d={`M ${x1} ${y1} C ${x1 + d} ${y1}, ${x2 - d} ${y2}, ${x2} ${y2}`}
            fill="none" stroke="#5b5b66" strokeWidth="1" strokeLinecap="round" opacity="0.9"
          />
        )
      })}
      {boxes.map((b) => {
        const tint = MINI_NODE_TINTS[b.type] ?? MINI_NODE_DEFAULT_TINT
        return (
          <rect
            key={b.id}
            x={tx(b.x)} y={ty(b.y)}
            width={Math.max(b.w * scale, 3)} height={Math.max(b.h * scale, 3)}
            rx="2" fill={tint.fill} stroke={tint.stroke} strokeWidth="0.75"
          />
        )
      })}
    </svg>
  )
}

// ─── Page ─────────────────────────────────────────────────────────────────────

export default function WorkflowsPage(): JSX.Element {
  const { t } = useI18n()
  const apiUrl = useAppStore((s) => s.apiUrl)
  const { workflows, loading, activeId, openIds, folders, folderColors, bookmarkedFolders, load, save, remove, importFile, exportFile, setActive, openWorkflow, closeWorkflow, moveOpenTab, addFolder, removeFolder, setFolderColor, toggleFolderBookmark } = useWorkflowsStore()
  const { modelNodePacks, processNodePacks, loadNodePacks } = useNodePacksStore()
  const pendingWorkflowNodePackId = useNavStore((s) => s.pendingWorkflowNodePackId)
  const consumeWorkflowNodePack = useNavStore((s) => s.consumeWorkflowNodePack)

  const [panelOpen, setPanelOpen] = useState(true)
  const [tabMenu, setTabMenu] = useState<{ id: string; x: number; y: number } | null>(null)
  const [renameTarget, setRenameTarget] = useState<{ id: string; value: string } | null>(null)
  const [openListVisible, setOpenListVisible] = useState(false)
  const [openSearch, setOpenSearch] = useState('')
  const [newFolderName, setNewFolderName] = useState<string | null>(null)   // null = input hidden
  const [collapsedFolders, setCollapsedFolders] = useState<Set<string>>(new Set())
  const [dragOverFolder, setDragOverFolder] = useState<string | null>(null) // '' = root area
  const [dragOverTab, setDragOverTab] = useState<string | null>(null)
  const [deleteTarget, setDeleteTarget] = useState<string | null>(null)     // workflow id pending deletion
  const [colorPickerFolder, setColorPickerFolder] = useState<string | null>(null)

  // Folder color of a workflow, if it lives in a colored folder
  const workflowColor = (wf: Workflow): string | undefined =>
    wf.folder ? folderColors[wf.folder] : undefined

  // Tab of the workflow currently executing (dot indicator), if any
  const runningWorkflowId = useWorkflowRunStore((s) =>
    s.runState.status === 'running' ? s.activeWorkflowId : null,
  )

  // Close the tab context menu on any outside click (it has no backdrop of its own)
  useEffect(() => {
    if (!tabMenu) return
    const close = (): void => setTabMenu(null)
    window.addEventListener('mousedown', close)
    return () => window.removeEventListener('mousedown', close)
  }, [tabMenu])

  // Escape closes whichever popup is topmost, regardless of what's focused —
  // relying on a focused element's own onKeyDown would miss presses right after
  // a popup opens (before anything inside it has focus).
  useEffect(() => {
    const onKey = (e: KeyboardEvent): void => {
      if (e.key !== 'Escape') return
      if (deleteTarget)          { setDeleteTarget(null); return }
      if (renameTarget)          { setRenameTarget(null); return }
      if (newFolderName !== null) { setNewFolderName(null); return }
      if (tabMenu)               { setTabMenu(null); return }
      if (openListVisible)       { setOpenListVisible(false); return }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [deleteTarget, renameTarget, newFolderName, tabMenu, openListVisible])

  // Browser-style tab shortcuts: Ctrl+T new, Ctrl+W close, Ctrl(+Shift)+Tab cycle
  useEffect(() => {
    const onKey = (e: KeyboardEvent): void => {
      if (!(e.ctrlKey || e.metaKey)) return
      if (e.key === 't' && !e.shiftKey) {
        e.preventDefault()
        handleCreateBlank()
      } else if (e.key === 'w' && !e.shiftKey) {
        e.preventDefault()
        if (activeId) handleCloseTab(activeId)
      } else if (e.key === 'Tab') {
        e.preventDefault()
        if (openIds.length < 2 || !activeId) return
        const idx  = openIds.indexOf(activeId)
        const next = e.shiftKey ? (idx - 1 + openIds.length) % openIds.length : (idx + 1) % openIds.length
        setActive(openIds[next])
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
    // eslint-disable-next-line react-hooks/exhaustive-deps -- store setters are stable; handleCreateBlank only uses them
  }, [activeId, openIds])

  // Fresh search / closed color picker each time the Open popup opens
  useEffect(() => {
    if (!openListVisible) { setOpenSearch(''); setColorPickerFolder(null) }
  }, [openListVisible])

  const storeNodePacks = useMemo(
    () => buildAllWorkflowNodePacks(modelNodePacks, processNodePacks),
    [modelNodePacks, processNodePacks],
  )
  const [serverNodePacks, setServerNodePacks] = useState<WorkflowNodePack[] | null>(null)

  // Prefer the unified server node schema. Fall back to the cached runtime list
  // only while the API is still coming online.
  useEffect(() => {
    let alive = true
    if (!apiUrl) return
    fetchWorkflowNodePacks(apiUrl)
      .then((list) => { if (alive) setServerNodePacks(list) })
      .catch(() => {})
    return () => { alive = false }
  }, [apiUrl])

  const allNodePacks = serverNodePacks ?? storeNodePacks

  // Load once on mount; the tab strip below keeps its height while this runs.
  // That prevents the late-arriving new-tab button from moving the editor.
  // eslint-disable-next-line react-hooks/exhaustive-deps -- load once on mount
  useEffect(() => { load(); loadNodePacks() }, [])

  // The Node Packs page can hand off a concrete node to this page. Consume the
  // intent only after the node pack catalogue is available, then open a ready
  // starter workflow instead of leaving the user on an empty canvas.
  useEffect(() => {
    if (!pendingWorkflowNodePackId || loading) return
    const nodePack = allNodePacks.find((item) => item.id === pendingWorkflowNodePackId)
    if (!nodePack) return
    if (consumeWorkflowNodePack() !== pendingWorkflowNodePackId) return
    const workflow = newWorkflow(nodePack, t)
    void save(workflow).then((result) => {
      if (result.success) openWorkflow(workflow.id)
    })
  }, [allNodePacks, consumeWorkflowNodePack, loading, openWorkflow, pendingWorkflowNodePackId, save, t])

  // Auto-select the first open tab when none is active or the active id is gone
  useEffect(() => {
    if (loading) return
    if (openIds.length === 0) return
    if (activeId && openIds.includes(activeId) && workflows.find((w) => w.id === activeId)) return
    setActive(openIds[0])
    // eslint-disable-next-line react-hooks/exhaustive-deps -- setActive is a stable store setter
  }, [workflows, loading, activeId, openIds])

  const openWorkflows  = openIds.map((id) => workflows.find((w) => w.id === id)).filter((w): w is Workflow => !!w)
  const activeWorkflow = workflows.find((w) => w.id === activeId) ?? null

  async function handleCreateBlank() {
    const wf = newWorkflow(undefined, t)
    await save(wf)
    openWorkflow(wf.id)
  }

  async function handleCreateWorkflowFromTemplate(template: WorkflowTemplate) {
    const wf = instantiateWorkflowTemplate(template)
    await save(wf)
    openWorkflow(wf.id)
  }

  async function handleImport() {
    const result = await importFile()
    if (result.success && result.workflow) openWorkflow((result.workflow as Workflow).id)
  }

  async function handleRename() {
    if (!renameTarget) return
    const wf = workflows.find((w) => w.id === renameTarget.id)
    const trimmed = renameTarget.value.trim()
    if (wf && trimmed && trimmed !== wf.name) {
      await save({ ...wf, name: trimmed, updatedAt: new Date().toISOString() })
    }
    setRenameTarget(null)
  }

  function renderWorkflowCard(wf: Workflow): JSX.Element {
    const isOpen = openIds.includes(wf.id)
    const cardActionCls = 'flex items-center justify-center w-5 h-5 rounded-md bg-card/95 backdrop-blur-sm text-muted-foreground opacity-0 group-hover:opacity-100 transition-all hover:scale-110'
    return (
      <div
        key={wf.id}
        draggable
        onDragStart={(e) => {
          e.dataTransfer.setData(WORKFLOW_DRAG_KEY, wf.id)
          e.dataTransfer.effectAllowed = 'move'
        }}
        onClick={() => { openWorkflow(wf.id); setOpenListVisible(false) }}
        className={`group relative flex flex-col rounded-lg overflow-hidden border cursor-pointer transition-colors
          ${isOpen ? 'border-primary/50 bg-primary/10' : 'border-border bg-card/60 hover:border-primary/30 hover:bg-muted/60'}`}
      >
        <div className="relative h-[72px] border-b border-border/70 bg-background/60">
          {workflowColor(wf) && (
            <div
              className="absolute inset-0 pointer-events-none"
              style={{ background: `radial-gradient(ellipse 90% 110% at 50% 45%, ${workflowColor(wf)}30, ${workflowColor(wf)}08 60%, transparent 80%)` }}
            />
          )}
          <WorkflowMiniPreview wf={wf} />
          <div className="absolute top-1 right-1 flex items-center gap-0.5">
            <button
              onClick={(e) => { e.stopPropagation(); handleToggleBookmark(wf.id) }}
              title={wf.bookmarked ? t('workflows.removeBookmark') : t('workflows.bookmark')}
              className={`flex items-center justify-center w-5 h-5 rounded-md bg-card/95 backdrop-blur-sm opacity-0 group-hover:opacity-100 transition-all hover:scale-110
                ${wf.bookmarked ? 'text-amber-400 drop-shadow-[0_0_5px_rgba(251,191,36,0.55)]' : 'text-muted-foreground hover:text-amber-300'}`}
            >
              <svg width="11" height="11" viewBox="0 0 24 24" fill={wf.bookmarked ? 'currentColor' : 'none'} stroke="currentColor" strokeWidth="1.75" strokeLinejoin="round">
                <polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/>
              </svg>
            </button>
            <button
              onClick={(e) => { e.stopPropagation(); handleDuplicate(wf.id) }}
              title={t('workflows.duplicate')}
              className={`${cardActionCls} hover:bg-muted hover:text-foreground`}
            >
              <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>
              </svg>
            </button>
            <button
              onClick={(e) => { e.stopPropagation(); setRenameTarget({ id: wf.id, value: wf.name }) }}
              title={t('workflows.rename')}
              className={`${cardActionCls} hover:bg-muted hover:text-foreground`}
            >
              <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <path d="M17 3a2.828 2.828 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5L17 3z"/>
              </svg>
            </button>
            <button
              onClick={(e) => { e.stopPropagation(); setDeleteTarget(wf.id) }}
              title={t('workflows.delete')}
              className={`${cardActionCls} hover:bg-destructive/10 hover:text-destructive`}
            >
              <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <polyline points="3 6 5 6 21 6"/>
                <path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/>
              </svg>
            </button>
          </div>
        </div>
        <div className="flex min-h-[2.75rem] flex-col justify-between px-2.5 py-1.5">
          <div className="flex items-center gap-1.5 min-w-0">
            <p className="min-w-0 truncate text-xs font-medium leading-4 text-foreground">{wf.name || t('workflows.untitled')}</p>
            {wf.templateId && <span className="shrink-0 rounded border border-primary/25 px-1 py-px text-[8px] text-primary">{t('workflows.templateBadge')}</span>}
          </div>
          <p className="truncate text-[10px] leading-4 text-muted-foreground">{new Date(wf.updatedAt).toLocaleString()}</p>
        </div>
      </div>
    )
  }

  function renderFolder(folder: string): JSX.Element {
    const inFolder  = workflows.filter((w) => w.folder === folder).sort((a, b) => b.updatedAt.localeCompare(a.updatedAt))
    const collapsed = collapsedFolders.has(folder)
    return (
      <div key={folder}>
        <div
          onClick={() => setCollapsedFolders((s) => {
            const next = new Set(s)
            if (next.has(folder)) next.delete(folder); else next.add(folder)
            return next
          })}
          onDragOver={(e) => { e.preventDefault(); e.stopPropagation(); setDragOverFolder(folder) }}
          onDragLeave={() => setDragOverFolder(null)}
          onDrop={(e) => {
            e.preventDefault()
            e.stopPropagation()
            setDragOverFolder(null)
            const id = readDragData(e.dataTransfer, WORKFLOW_DRAG_KEY)
            if (id) handleMoveToFolder(id, folder)
          }}
          className={`group flex cursor-pointer items-center gap-2 px-4 py-2 text-muted-foreground transition-colors hover:text-foreground
            ${dragOverFolder === folder ? 'bg-primary/10 text-primary' : ''}`}
        >
          <svg width="9" height="9" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"
            className={`shrink-0 transition-transform ${collapsed ? '' : 'rotate-90'}`}>
            <polyline points="9 18 15 12 9 6"/>
          </svg>
          <svg width="12" height="12" viewBox="0 0 24 24" fill={folderColors[folder] ? `${folderColors[folder]}33` : 'none'} stroke={folderColors[folder] ?? 'currentColor'} strokeWidth="2" className="shrink-0">
            <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/>
          </svg>
          <span className="text-xs font-medium truncate">{folder}</span>
          <span className="text-[10px] text-muted-foreground">{inFolder.length}</span>
          <div className="flex-1" />
          <button
            onClick={(e) => { e.stopPropagation(); toggleFolderBookmark(folder) }}
            title={bookmarkedFolders.includes(folder) ? t('workflows.removeBookmark') : t('workflows.bookmarkFolder')}
            className={`flex h-5 w-5 shrink-0 items-center justify-center rounded-md opacity-0 transition-all hover:scale-110 hover:bg-muted group-hover:opacity-100
              ${bookmarkedFolders.includes(folder) ? 'text-amber-400 drop-shadow-[0_0_5px_rgba(251,191,36,0.55)]' : 'text-muted-foreground hover:text-amber-300'}`}
          >
            <svg width="11" height="11" viewBox="0 0 24 24" fill={bookmarkedFolders.includes(folder) ? 'currentColor' : 'none'} stroke="currentColor" strokeWidth="1.75" strokeLinejoin="round">
              <polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/>
            </svg>
          </button>
          <button
            onClick={(e) => { e.stopPropagation(); setColorPickerFolder((cur) => (cur === folder ? null : folder)) }}
            title={t('workflows.folderColor')}
            className="flex h-5 w-5 shrink-0 items-center justify-center rounded opacity-0 transition-all hover:bg-muted group-hover:opacity-100"
          >
            <span className="h-2.5 w-2.5 rounded-full border border-border" style={{ background: folderColors[folder] ?? 'transparent' }} />
          </button>
          <button
            onClick={(e) => { e.stopPropagation(); handleDeleteFolder(folder) }}
            title={t('workflows.deleteFolder')}
            className="flex h-5 w-5 shrink-0 items-center justify-center rounded text-muted-foreground/70 opacity-0 transition-all hover:bg-destructive/10 hover:text-destructive group-hover:opacity-100"
          >
            <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <polyline points="3 6 5 6 21 6"/>
              <path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/>
            </svg>
          </button>
        </div>
        {colorPickerFolder === folder && (
          <div className="flex items-center gap-1.5 pl-11 pr-4 py-1.5">
            {FOLDER_COLORS.map((c) => (
              <button
                key={c}
                onClick={() => { setFolderColor(folder, c); setColorPickerFolder(null) }}
                className={`h-4 w-4 rounded-full transition-transform hover:scale-125 ${folderColors[folder] === c ? 'ring-2 ring-ring ring-offset-1 ring-offset-card' : ''}`}
                style={{ background: c }}
              />
            ))}
          </div>
        )}
        {!collapsed && inFolder.length > 0 && (
          <div className="grid grid-cols-3 gap-2 pl-8 pr-4 py-1.5">
            {inFolder.map((wf) => renderWorkflowCard(wf))}
          </div>
        )}
        {!collapsed && inFolder.length === 0 && (
          <p className="py-1.5 pl-11 pr-5 text-[10px] italic text-muted-foreground/70">{t('workflows.emptyFolder')}</p>
        )}
      </div>
    )
  }

  // Closing the tab of an empty workflow (no nodes) deletes it too — a blank
  // "New Workflow" the user closes is throwaway, don't let them pile up on disk.
  // Reads the store directly so a keyboard shortcut never acts on a stale list.
  function handleCloseTab(id: string) {
    const wf = useWorkflowsStore.getState().workflows.find((w) => w.id === id)
    if (wf && wf.nodes.length === 0) { remove(id); return }
    closeWorkflow(id)
  }

  async function handleToggleBookmark(id: string) {
    const wf = workflows.find((w) => w.id === id)
    if (!wf) return
    // Not an edit — keep updatedAt so the recency sort doesn't reshuffle
    await save({ ...wf, bookmarked: !wf.bookmarked })
  }

  async function handleMoveToFolder(id: string, folder?: string) {
    const wf = workflows.find((w) => w.id === id)
    if (!wf || (wf.folder ?? undefined) === folder) return
    await save({ ...wf, folder })
  }

  async function handleDeleteFolder(name: string) {
    for (const wf of workflows.filter((w) => w.folder === name)) {
      await save({ ...wf, folder: undefined })
    }
    removeFolder(name)
  }

  function handleCreateFolder() {
    const trimmed = (newFolderName ?? '').trim()
    if (trimmed) addFolder(trimmed)
    setNewFolderName(null)
  }

  async function handleDuplicate(id: string) {
    const src = workflows.find((w) => w.id === id)
    if (!src) return
    const now = new Date().toISOString()
    const copy: Workflow = {
      ...structuredClone(src),
      id:         newId(),
      name:       `${src.name || t('workflows.untitled')} ${t('workflows.copySuffix')}`,
      bookmarked: undefined,   // a copy isn't the favorite
      createdAt:  now,
      updatedAt:  now,
    }
    await save(copy)
    openWorkflow(copy.id)
  }

  return (
    <div className="flex flex-col flex-1 overflow-hidden">

      {/* Tab bar */}
      <div className="flex h-10 shrink-0 items-stretch overflow-x-auto border-b border-border/45 bg-card/55">
        {loading ? (
          <div className="h-full w-24 animate-pulse bg-muted/20" aria-hidden="true" />
        ) : (
          <>
          {openWorkflows.map((wf) => (
            <div
              key={wf.id}
              draggable
              onDragStart={(e) => { e.dataTransfer.setData(TAB_DRAG_KEY, wf.id); e.dataTransfer.effectAllowed = 'move' }}
              onDragOver={(e) => {
                if (!e.dataTransfer.types.includes(TAB_DRAG_KEY)) return
                e.preventDefault()
                setDragOverTab(wf.id)
              }}
              onDragLeave={() => setDragOverTab((cur) => (cur === wf.id ? null : cur))}
              onDrop={(e) => {
                e.preventDefault()
                setDragOverTab(null)
                const dragId = readDragData(e.dataTransfer, TAB_DRAG_KEY)
                if (dragId) moveOpenTab(dragId, wf.id)
              }}
              onClick={() => setActive(wf.id)}
              onMouseDown={(e) => { if (e.button === 1) { e.preventDefault(); handleCloseTab(wf.id) } }}
              onContextMenu={(e) => { e.preventDefault(); setTabMenu({ id: wf.id, x: e.clientX, y: e.clientY }) }}
              className={`relative flex items-center gap-1.5 pl-3 pr-1.5 h-full text-[11px] font-medium shrink-0 transition-colors border-b-2 cursor-pointer group
                ${wf.id === activeId
                  ? 'border-primary bg-muted/80 text-foreground'
                  : 'border-transparent text-muted-foreground hover:bg-muted/40 hover:text-foreground'
                }`}
            >
              {dragOverTab === wf.id && <span className="absolute bottom-1 left-0 top-1 w-0.5 rounded bg-primary" />}
              {/* Folder color dot; doubles as the running indicator (pulses) */}
              {(workflowColor(wf) || runningWorkflowId === wf.id) && (
                <span
                  className={`h-1.5 w-1.5 shrink-0 rounded-full ${runningWorkflowId === wf.id ? 'animate-pulse' : ''} ${workflowColor(wf) ? '' : 'bg-primary'}`}
              title={runningWorkflowId === wf.id ? t('workflows.runningTab') : undefined}
                  style={workflowColor(wf) ? { background: workflowColor(wf), boxShadow: `0 0 4px ${workflowColor(wf)}80` } : undefined}
                />
              )}
              <span className="truncate max-w-[120px]">{wf.name || t('workflows.untitled')}</span>
              <button
                onClick={(e) => { e.stopPropagation(); handleCloseTab(wf.id) }}
                title={t('workflows.closeTab')}
                className="flex h-5 w-5 shrink-0 items-center justify-center rounded text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
              >
                <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
                  <line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>
                </svg>
              </button>
            </div>
          ))}
          <button
            onClick={handleCreateBlank}
            title={t('workflows.newWorkflow')}
            className="flex h-full w-9 shrink-0 items-center justify-center text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
              <line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/>
            </svg>
          </button>
          </>
        )}
      </div>

      {/* Tab context menu */}
      {tabMenu && (
        <div
          style={{ left: tabMenu.x, top: tabMenu.y }}
          className="fixed z-50 min-w-[140px] rounded-lg border border-border bg-popover py-1 shadow-xl"
          onMouseDown={(e) => e.stopPropagation()}
        >
          <button
            onClick={() => {
              const wf = workflows.find((w) => w.id === tabMenu.id)
              if (wf) setRenameTarget({ id: wf.id, value: wf.name })
              setTabMenu(null)
            }}
            className="flex w-full items-center gap-2 px-3 py-1.5 text-[11px] text-popover-foreground transition-colors hover:bg-muted"
          >
            <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="shrink-0">
              <path d="M17 3a2.828 2.828 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5L17 3z"/>
            </svg>
            {t('workflows.rename')}
          </button>
          <button
            onClick={() => { handleDuplicate(tabMenu.id); setTabMenu(null) }}
            className="flex w-full items-center gap-2 px-3 py-1.5 text-[11px] text-popover-foreground transition-colors hover:bg-muted"
          >
            <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="shrink-0">
              <rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>
            </svg>
            {t('workflows.duplicate')}
          </button>
          <button
            onClick={() => {
              const wf = workflows.find((w) => w.id === tabMenu.id)
              if (wf) exportFile(wf)
              setTabMenu(null)
            }}
            className="flex w-full items-center gap-2 px-3 py-1.5 text-[11px] text-popover-foreground transition-colors hover:bg-muted"
          >
            <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="shrink-0">
              <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
              <polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/>
            </svg>
            {t('workflows.exportJson')}
          </button>
          <div className="my-1 h-px bg-border" />
          <button
            onClick={() => { setDeleteTarget(tabMenu.id); setTabMenu(null) }}
            className="flex w-full items-center gap-2 px-3 py-1.5 text-[11px] text-muted-foreground transition-colors hover:bg-destructive/10 hover:text-destructive"
          >
            <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="shrink-0">
              <polyline points="3 6 5 6 21 6"/>
              <path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/>
            </svg>
            {t('workflows.delete')}
          </button>
        </div>
      )}

      {/* Rename popup (above the Open popup, which can trigger it) */}
      <Dialog open={!!renameTarget} onOpenChange={(open) => { if (!open) setRenameTarget(null) }}>
        <DialogContent className="sm:max-w-[360px]">
          <DialogHeader>
            <DialogTitle>{t('workflows.renameTitle')}</DialogTitle>
            <DialogDescription>{t('workflows.renameDescription')}</DialogDescription>
          </DialogHeader>
          <Input
            autoFocus
            value={renameTarget?.value ?? ''}
            onChange={(e) => setRenameTarget((current) => current ? { ...current, value: e.target.value } : current)}
            onFocus={(e) => e.currentTarget.select()}
            onKeyDown={(e) => { if (e.key === 'Enter') handleRename() }}
            placeholder={`${t('workflows.workflowName')}…`}
            aria-label={t('workflows.workflowName')}
          />
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => setRenameTarget(null)}>{t('common.cancel')}</Button>
            <Button type="button" onClick={handleRename} disabled={!renameTarget?.value.trim()}>{t('workflows.rename')}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Delete confirmation popup (topmost — reachable from the Open popup) */}
      <Dialog open={!!deleteTarget} onOpenChange={(open) => { if (!open) setDeleteTarget(null) }}>
        <DialogContent className="sm:max-w-[360px]">
          <DialogHeader>
            <DialogTitle>{t('workflows.deleteTitle')}</DialogTitle>
            <DialogDescription>
              {t('workflows.deleteDescription', { name: workflows.find((w) => w.id === deleteTarget)?.name || t('workflows.untitled') })}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => setDeleteTarget(null)}>{t('common.cancel')}</Button>
            <Button type="button" variant="destructive" onClick={() => { if (deleteTarget) remove(deleteTarget); setDeleteTarget(null) }}>{t('workflows.delete')}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Open workflow dialog */}
      <Dialog open={openListVisible} onOpenChange={setOpenListVisible}>
        <DialogContent className="flex max-h-[70vh] w-[640px] max-w-[92vw] flex-col overflow-hidden p-0">
            <DialogHeader className="flex-row items-center justify-between space-y-0 border-b border-border px-5 py-4 pr-12">
              <DialogTitle className="text-sm font-semibold text-foreground">{t('workflows.open')}</DialogTitle>
              <DialogDescription className="sr-only">{t('workflows.openDescription')}</DialogDescription>
              <div className="flex items-center gap-1">
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  className="h-7 w-7"
                  onClick={() => setNewFolderName('')}
                  title={t('workflows.newFolder')}
                >
                  <FolderPlus className="size-4" aria-hidden="true" />
                </Button>
              </div>
            </DialogHeader>

            {/* Search */}
            <div className="border-b border-border px-4 py-2">
              <div className="flex items-center gap-2 rounded-md border border-input bg-background px-2.5 py-1.5 focus-within:border-ring">
                <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="shrink-0 text-muted-foreground">
                  <circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/>
                </svg>
                <input
                  autoFocus
                  value={openSearch}
                  onChange={(e) => setOpenSearch(e.target.value)}
                  placeholder={t('workflows.search')}
                  className="min-w-0 flex-1 bg-transparent text-xs text-foreground placeholder:text-muted-foreground focus:outline-none"
                />
                {openSearch && (
                  <button onClick={() => setOpenSearch('')} className="shrink-0 text-muted-foreground transition-colors hover:text-foreground">
                    <svg width="9" height="9" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
                      <line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>
                    </svg>
                  </button>
                )}
              </div>
            </div>

            {/* New folder inline input */}
            {newFolderName !== null && (
              <div className="flex items-center gap-2 border-b border-border bg-muted/40 px-4 py-2">
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="shrink-0 text-muted-foreground">
                  <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1 2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/>
                </svg>
                <input
                  autoFocus
                  value={newFolderName}
                  onChange={(e) => setNewFolderName(e.target.value)}
                  onKeyDown={(e) => { if (e.key === 'Enter') handleCreateFolder() }}
                  onBlur={() => setNewFolderName(null)}
                  placeholder={t('workflows.folderName')}
                  className="min-w-0 flex-1 rounded-md border border-input bg-background px-2.5 py-1.5 text-xs text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring"
                />
              </div>
            )}

            <div
              className="flex-1 overflow-y-auto py-1"
              onDragOver={(e) => e.preventDefault()}
              onDrop={(e) => {
                // Drop on the list background (not a folder) → move back to root
                e.preventDefault()
                setDragOverFolder(null)
                const id = readDragData(e.dataTransfer, WORKFLOW_DRAG_KEY)
                if (id) handleMoveToFolder(id, undefined)
              }}
            >
              {workflows.length === 0 && folders.length === 0 && (
                <p className="px-5 py-6 text-center text-xs italic text-muted-foreground">{t('workflows.noSavedWorkflows')}</p>
              )}

              {/* Search results — flat list across all folders */}
              {openSearch.trim() !== '' && (() => {
                const q = openSearch.trim().toLowerCase()
                const matches = workflows
                  .filter((w) => (w.name || t('workflows.untitled')).toLowerCase().includes(q))
                  .sort((a, b) => b.updatedAt.localeCompare(a.updatedAt))
                if (matches.length === 0) {
                  return <p className="px-5 py-6 text-center text-xs italic text-muted-foreground">{t('workflows.noWorkflowMatches', { query: openSearch.trim() })}</p>
                }
                return (
                  <div className="grid grid-cols-3 gap-2 px-4 py-2">
                    {matches.map((wf) => renderWorkflowCard(wf))}
                  </div>
                )
              })()}

              {/* Bookmarks — pinned section: starred folders, then starred workflows */}
              {openSearch.trim() === '' && (() => {
                const markedFolders = [...bookmarkedFolders].filter((f) => folders.includes(f)).sort((a, b) => a.localeCompare(b))
                // Starred workflows already shown inside a starred folder aren't repeated
                const marked = workflows
                  .filter((w) => w.bookmarked && !(w.folder && bookmarkedFolders.includes(w.folder)))
                  .sort((a, b) => b.updatedAt.localeCompare(a.updatedAt))
                if (marked.length === 0 && markedFolders.length === 0) return null
                return (
                  <div className="mb-1 border-b border-border/70 pb-1">
                    <div className="flex items-center gap-2 px-4 py-2 text-amber-400/80">
                      <svg width="11" height="11" viewBox="0 0 24 24" fill="currentColor" className="shrink-0">
                        <polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/>
                      </svg>
                      <span className="text-xs font-medium">{t('workflows.bookmarks')}</span>
                    </div>
                    {markedFolders.map((folder) => renderFolder(folder))}
                    {marked.length > 0 && (
                      <div className="grid grid-cols-3 gap-2 px-4 py-1.5">
                        {marked.map((wf) => renderWorkflowCard(wf))}
                      </div>
                    )}
                  </div>
                )
              })()}

              {/* Folders (bookmarked ones live in the section above) */}
              {openSearch.trim() === '' && [...folders]
                .filter((f) => !bookmarkedFolders.includes(f))
                .sort((a, b) => a.localeCompare(b))
                .map((folder) => renderFolder(folder))}

              {/* Root workflows */}
              {openSearch.trim() === '' && (() => {
                const root = workflows
                  .filter((w) => !w.folder || !folders.includes(w.folder))
                  .sort((a, b) => b.updatedAt.localeCompare(a.updatedAt))
                if (root.length === 0) return null
                return (
                  <div className="grid grid-cols-3 gap-2 px-4 py-2">
                    {root.map((wf) => renderWorkflowCard(wf))}
                  </div>
                )
              })()}
            </div>
        </DialogContent>
      </Dialog>

      {/* Node library + canvas + output products */}
      <div className="flex flex-1 overflow-hidden">

        <NodeLibraryPanel allNodePacks={allNodePacks} open={panelOpen} onUseTemplate={handleCreateWorkflowFromTemplate} />

        {activeWorkflow ? (
          <ReactFlowProvider>
            <WorkflowCanvasInner
              key={activeWorkflow.id}
              workflow={activeWorkflow}
              allNodePacks={allNodePacks}
              onSave={save}
              panelOpen={panelOpen}
              onTogglePanel={() => setPanelOpen((o) => !o)}
              onOpen={() => setOpenListVisible(true)}
              onImport={handleImport}
            />
          </ReactFlowProvider>
        ) : (
          <div className="flex flex-1 flex-col items-center justify-center gap-3 text-muted-foreground">
            <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1">
              <rect x="3" y="3" width="6" height="5" rx="1"/><rect x="3" y="11" width="6" height="5" rx="1"/>
              <path d="M9 5.5h3.5a1 1 0 0 1 1 1v5"/><rect x="13" y="9" width="8" height="7" rx="1"/>
            </svg>
            <div className="text-center">
              <p className="text-sm font-medium text-foreground">{workflows.length === 0 ? t('workflows.noWorkflows') : t('workflows.noOpen')}</p>
              <p className="mt-1 text-xs text-muted-foreground">{workflows.length === 0 ? t('workflows.createOne') : t('workflows.openSaved')}</p>
            </div>
            <div className="flex items-center gap-2 mt-2">
              {workflows.length > 0 && (
                <button onClick={() => setOpenListVisible(true)} className="rounded-md bg-primary px-4 py-2 text-xs font-semibold text-primary-foreground transition-colors hover:bg-primary/90">
                  {t('workflows.open')}
                </button>
              )}
              <button onClick={handleCreateBlank} className={`rounded-md px-4 py-2 text-xs font-semibold transition-colors ${workflows.length === 0 ? 'bg-primary text-primary-foreground hover:bg-primary/90' : 'border border-input bg-background text-foreground hover:bg-muted'}`}>
                {t('workflows.new')}
              </button>
              <button onClick={handleImport} className="rounded-md border border-input bg-background px-4 py-2 text-xs font-semibold text-foreground transition-colors hover:bg-muted">
                {t('workflows.import')}
              </button>
            </div>
          </div>
        )}

        <WorkflowOutputsPanel workflowId={activeWorkflow?.id} />
      </div>
    </div>
  )
}

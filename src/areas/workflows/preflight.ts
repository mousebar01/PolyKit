import type { Workflow, WFNode } from '@shared/types/runtime.d'
import { getWorkflowNodePack, type WorkflowNodePack } from './mockNodePacks'

type DataType = 'image' | 'text' | 'mesh' | 'audio'

export interface WorkflowPreflightIssue {
  key: string
  message: string
  nodeId?: string
}

export interface WorkflowPreflightOptions {
  executionMode?: 'web'
  /** Base64 of the image currently selected in the app (fallback for Image nodes). */
  selectedImageData?: string | null
}

export function nodeLabel(node: WFNode, allNodePacks: WorkflowNodePack[]): string {
  if (node.type === 'imageNode') return 'Image'
  if (node.type === 'textNode') return 'Text'
  if (node.type === 'meshNode') return 'Load 3D Mesh'
  if (node.type === 'outputNode') return 'Output'
  if (node.type === 'previewNode') return 'Preview'
  if (node.type === 'noteNode') return 'Note'
  if (node.type === 'nodePackNode') {
    return getWorkflowNodePack(node.data.nodePackId ?? '', allNodePacks)?.name ?? 'Node pack'
  }
  return 'Node'
}

function formatType(type: DataType): string {
  if (type === 'mesh') return 'mesh'
  if (type === 'image') return 'image'
  if (type === 'audio') return 'audio'
  return 'text'
}

function formatRequiredTypes(types: DataType[]): string {
  if (types.length === 1) return formatType(types[0])
  if (types.length === 2) return `${formatType(types[0])} and ${formatType(types[1])}`
  return `${types.slice(0, -1).map(formatType).join(', ')}, and ${formatType(types[types.length - 1])}`
}

function getNodeOutputType(node: WFNode, allNodePacks: WorkflowNodePack[]): DataType | undefined {
  if (node.type === 'imageNode') return 'image'
  if (node.type === 'textNode') return 'text'
  if (node.type === 'meshNode' || node.type === 'outputNode') return 'mesh'
  if (node.type === 'previewNode') return 'image'
  if (node.type === 'nodePackNode') {
    return getWorkflowNodePack(node.data.nodePackId ?? '', allNodePacks)?.output
  }
  return undefined
}

function pushIssue(issues: WorkflowPreflightIssue[], issue: WorkflowPreflightIssue): void {
  if (!issues.some((existing) => existing.key === issue.key)) issues.push(issue)
}

export function validateWorkflowPreflight(
  workflow: Workflow,
  allNodePacks: WorkflowNodePack[],
  options: WorkflowPreflightOptions = {},
): WorkflowPreflightIssue[] {
  const issues: WorkflowPreflightIssue[] = []
  const nodeMap = new Map(workflow.nodes.map((node) => [node.id, node]))

  if (options.executionMode === 'web') {
    // The headless server executes the same node packs as the editor. The base
    // node set is the
    // ComfyUI-style linear DAG (sources → model/process node packs → sinks) plus
    // inert Note nodes; anything else (e.g. local mesh loads) is called out by
    // name instead of pointing users at a different shell.
    const allowed = new Set(['imageNode', 'textNode', 'meshNode', 'nodePackNode', 'outputNode', 'previewNode', 'noteNode'])
    const unsupported = workflow.nodes.filter((node) => !allowed.has(node.type))
    if (unsupported.length > 0) {
      const labels = [...new Set(unsupported.map((node) => nodeLabel(node, allNodePacks)))]
      pushIssue(issues, {
        key: 'web-execution-unsupported',
        nodeId: unsupported[0]!.id,
        message: `This workflow uses ${labels.join(' · ')} — the server can't execute ${labels.length > 1 ? 'those nodes' : 'that node'} yet. Remove ${labels.length > 1 ? 'them' : 'it'} or simplify the graph.`,
      })
    }
  }

  const outputTypes = new Map<string, DataType | undefined>()
  for (const node of workflow.nodes) {
    outputTypes.set(node.id, getNodeOutputType(node, allNodePacks))
  }
  for (const node of workflow.nodes) {
    if (node.type === 'imageNode' && !(node.data.params?.filePath as string | undefined) && !options?.selectedImageData) {
      pushIssue(issues, {
        key: `${node.id}:no-image`,
        nodeId: node.id,
        message: `${nodeLabel(node, allNodePacks)} needs an image — click Browse on the node to select one.`,
      })
    }

    if (node.type === 'meshNode') {
      const filePath = node.data.params?.filePath as string | undefined
      if (!filePath) {
        pushIssue(issues, {
          key: `${node.id}:no-mesh`,
          nodeId: node.id,
          message: `${nodeLabel(node, allNodePacks)} needs a mesh — click "Upload local mesh…" or "From server…" on the node to pick one.`,
        })
      } else if (options.executionMode === 'web' && (filePath.startsWith('web-file://') || filePath.startsWith('/') || /^[A-Za-z]:[\\/]/.test(filePath))) {
        // A raw local path cannot be read by a (possibly remote) backend.
        pushIssue(issues, {
          key: `${node.id}:mesh-not-uploaded`,
          nodeId: node.id,
          message: `${nodeLabel(node, allNodePacks)} is a local file the server can't read — re-pick it via "Upload local mesh…" or "From server…".`,
        })
      }
    }

    if (node.type !== 'nodePackNode') continue

    const ext = getWorkflowNodePack(node.data.nodePackId ?? '', allNodePacks)
    if (!ext) {
      pushIssue(issues, {
        key: `${node.id}:missing-node-pack`,
        nodeId: node.id,
        message: `${nodeLabel(node, allNodePacks)} is unavailable. Reload node packs or remove the node.`,
      })
      continue
    }

    const incomingEdges = workflow.edges.filter((edge) => edge.target === node.id)
    const requiredTypes = [...new Set((ext.inputs ?? [ext.input]) as DataType[])]

    for (const requiredType of requiredTypes) {
      const hasMatchingInput = incomingEdges.some((edge) => outputTypes.get(edge.source) === requiredType)
      if (!hasMatchingInput) {
        pushIssue(issues, {
          key: `${node.id}:missing:${requiredType}`,
          nodeId: node.id,
          message: `${ext.name} needs an incoming ${formatType(requiredType)} connection.`,
        })
      }
    }

    for (const edge of incomingEdges) {
      const sourceNode = nodeMap.get(edge.source)
      const sourceType = outputTypes.get(edge.source)
      if (!sourceNode || !sourceType || requiredTypes.includes(sourceType)) continue
      pushIssue(issues, {
        key: `${node.id}:type:${edge.id}`,
        nodeId: node.id,
        message: `${ext.name} expects ${formatRequiredTypes(requiredTypes)}, but ${nodeLabel(sourceNode, allNodePacks)} outputs ${formatType(sourceType)}.`,
      })
    }
  }

  return issues
}

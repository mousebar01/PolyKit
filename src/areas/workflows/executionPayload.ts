import type { Workflow, WFNode } from '@shared/types/runtime.d'
import type { WorkflowNodePack } from './mockNodePacks'

export interface WorkflowExecutionNode {
  class_type: string
  inputs: Record<string, unknown>
}

export interface WorkflowExecutionRequest {
  schema_version: 1
  workflow_id: string
  prompt: Record<string, WorkflowExecutionNode>
  output_node_id?: string
  target_node_ids?: string[]
  collection: string
}

export interface CompileOptions {
  selectedImagePath?: string
  selectedImageData?: string
  /**
   * Optional per-node bindings used by headless/web callers. A binding is a
   * path already present in the server workspace; it deliberately takes
   * precedence over a node preview or the globally selected image.
   */
  imageNodeWorkspacePaths?: Record<string, string>
  /**
   * Compile only the selected node and its upstream dependencies. A node-pack
   * target gets a temporary server-owned sink so intermediate image/mesh
   * results can be published without changing the editable workflow graph.
   */
  targetNodeId?: string
}

const IMAGE_NODE = 'polykit.image'
const TEXT_NODE = 'polykit.text'
const OUTPUT_NODE = 'polykit.output'
const IMAGE_OUTPUT_NODE = 'polykit.image_output'
const MESH_NODE = 'polykit.mesh'
const PREVIEW_NODE = 'polykit.preview'

// Nodes compiled by the Web client must be executable by FastAPI. Note nodes
// are inert annotations and are omitted from the execution prompt.
const SERVER_NODE_TYPES = new Set(['imageNode', 'textNode', 'meshNode', 'nodePackNode', 'outputNode', 'previewNode'])
const INERT_NODE_TYPES = new Set(['noteNode'])

function outputNameOf(node: WFNode, allNodePacks: WorkflowNodePack[]): string {
  switch (node.type) {
    case 'imageNode': return 'image'
    case 'textNode': return 'text'
    case 'nodePackNode': {
      const ext = allNodePacks.find((item) => item.id === node.data.nodePackId)
      return ext?.output ?? 'mesh'
    }
    default: return 'mesh'
  }
}

function wireNodePackInputs(
  workflow: Workflow,
  nodeId: string,
  nodePack: WorkflowNodePack,
  allNodePacks: WorkflowNodePack[],
): Record<string, unknown> {
  const inputNames = nodePack.inputs?.length ? nodePack.inputs : [nodePack.input]
  const inputs: Record<string, unknown> = {}
  for (const edge of workflow.edges) {
    if (edge.target !== nodeId) continue
    const slot = edge.targetHandle === 'input-1' ? 1 : 0
    const name = inputNames[slot]
    if (!name) continue
    const source = workflow.nodes.find((node) => node.id === edge.source)
    if (!source) continue
    inputs[name] = [edge.source, outputNameOf(source, allNodePacks)]
  }
  return inputs
}

function nodeParams(node: WFNode, nodePack: WorkflowNodePack): Record<string, unknown> {
  const defaults = Object.fromEntries((nodePack.params ?? []).map((param) => [param.id, param.default]))
  return { ...defaults, ...(node.data.params ?? {}) }
}

/** Return true only for a safe path relative to the server workspace. */
export function isWorkspaceRelativePath(path: string | undefined): path is string {
  if (!path) return false
  const normalized = path.trim().replaceAll('\\', '/')
  if (!normalized || normalized.startsWith('/') || normalized.startsWith('web-file://')) return false
  if (/^[A-Za-z]:\//.test(normalized)) return false
  return !normalized.split('/').some((segment) => segment === '..')
}

async function readBase64(path: string | undefined, data: string | undefined): Promise<string | undefined> {
  if (data?.trim()) return data
  if (!path || path === '__blob__') return undefined
  try {
    return await window.polykit.fs.readFileBase64(path)
  } catch {
    return undefined
  }
}

export type ServerWorkflowCompile =
  | { ok: true; payload: WorkflowExecutionRequest }
  | { ok: false; error: string }

function sinkName(type: string): string {
  return type === 'outputNode' ? 'Output' : 'Preview'
}

/** Compile an editable workflow graph into the generic FastAPI execution prompt. */
export async function compileServerWorkflow(
  workflow: Workflow,
  allNodePacks: WorkflowNodePack[],
  options: CompileOptions = {},
): Promise<ServerWorkflowCompile> {
  const targetNode = options.targetNodeId
    ? workflow.nodes.find((node) => node.id === options.targetNodeId)
    : undefined
  if (options.targetNodeId && !targetNode) {
    return { ok: false, error: 'The selected run target no longer exists in this workflow.' }
  }

  const enabled = (node: WFNode): boolean => node.data.enabled !== false
  const executionNodeIds = targetNode
    ? upstreamNodeIds(workflow, targetNode.id)
    : new Set(workflow.nodes.map((node) => node.id))

  const unsupported = workflow.nodes.find(
    (node) => executionNodeIds.has(node.id) && !SERVER_NODE_TYPES.has(node.type) && !INERT_NODE_TYPES.has(node.type),
  )
  if (unsupported) {
    return {
      ok: false,
      error: `This workflow uses a "${unsupported.type}" node — the server can't execute it yet. Remove it or add server support for that node.`,
    }
  }

  const executable = workflow.nodes.filter(
    (node) => executionNodeIds.has(node.id) && node.type === 'nodePackNode' && enabled(node),
  )
  if (executable.length === 0) {
    return { ok: false, error: 'The workflow has no model or process node to run. Add one from the node library.' }
  }

  for (const node of executable) {
    const ext = allNodePacks.find((item) => item.id === node.data.nodePackId)
    if (!ext) {
      return {
        ok: false,
        error: `A node pack in this workflow is unavailable (${node.data.nodePackId}). Reload node packs or remove the node.`,
      }
    }
  }

  const imageSourceByNode = new Map<
    string,
    { kind: 'base64'; data: string } | { kind: 'workspace_path'; path: string }
  >()
  for (const node of workflow.nodes) {
    if (node.type !== 'imageNode' || !executionNodeIds.has(node.id)) continue
    const filePath = node.data.params?.filePath as string | undefined
    const hasExplicitWorkspaceBinding = Object.prototype.hasOwnProperty.call(
      options.imageNodeWorkspacePaths ?? {},
      node.id,
    )
    const explicitWorkspacePath = options.imageNodeWorkspacePaths?.[node.id]
    if (hasExplicitWorkspaceBinding) {
      if (!isWorkspaceRelativePath(explicitWorkspacePath)) {
        return {
          ok: false,
          error: `The Image node "${node.id}" must be bound to a workspace-relative path.`,
        }
      }
      imageSourceByNode.set(node.id, { kind: 'workspace_path', path: explicitWorkspacePath })
      continue
    }

    const isWorkspacePath = isWorkspaceRelativePath(filePath)
    if (isWorkspacePath) {
      imageSourceByNode.set(node.id, { kind: 'workspace_path', path: filePath })
      continue
    }

    const path = filePath ?? options.selectedImagePath
    const data = options.selectedImageData
    const preview = node.data.params?.preview as string | undefined
    const previewData = preview && preview.includes('base64,') ? preview.split('base64,')[1] : undefined
    const imageData = await readBase64(path, data ?? previewData)
    if (!imageData) {
      const staleHint = path && path.startsWith('web-file://')
        ? ' — the previously selected file is no longer available (the page was reloaded)'
        : ''
      return {
        ok: false,
        error: `An Image node has no readable image. Click Browse on the Image node to select one${staleHint}, then Run again.`,
      }
    }
    imageSourceByNode.set(node.id, { kind: 'base64', data: imageData })
  }

  if (targetNode && !enabled(targetNode)) {
    return { ok: false, error: 'The selected run target is disabled. Enable it and run again.' }
  }
  if (
    targetNode &&
    targetNode.type !== 'nodePackNode' &&
    targetNode.type !== 'outputNode' &&
    targetNode.type !== 'previewNode'
  ) {
    return { ok: false, error: 'Choose a model, process, Output, or Preview node to run to that point.' }
  }

  const enabledSinks = workflow.nodes.filter(
    (node) => executionNodeIds.has(node.id)
      && (node.type === 'outputNode' || node.type === 'previewNode')
      && enabled(node),
  )
  if (!targetNode && enabledSinks.length === 0) {
    return {
      ok: false,
      error: 'The output node is disabled or missing. Enable an "Output" node to receive the final mesh.',
    }
  }
  // A staged run only needs to validate its selected sink (if it has one).
  // Unrelated terminal nodes must not block running an earlier stage.
  for (const sink of enabledSinks.filter((candidate) => !targetNode || candidate.id === targetNode.id)) {
    if (!incomingSource(workflow, sink.id)) {
      return {
        ok: false,
        error: sink.type === 'outputNode'
          ? `The "${sinkName(sink.type)}" node isn't connected to a mesh. Connect a model or process node to it.`
          : `The "${sinkName(sink.type)}" node isn't connected to an output. Connect a model or process node to it.`,
      }
    }
  }
  const prompt: Record<string, WorkflowExecutionNode> = {}
  let outputNodeId: string | undefined

  for (const node of workflow.nodes) {
    if (!executionNodeIds.has(node.id)) continue
    if (node.type === 'imageNode') {
      prompt[node.id] = {
        class_type: IMAGE_NODE,
        inputs: { image: imageSourceByNode.get(node.id)! },
      }
    } else if (node.type === 'textNode') {
      const text = (node.data.params?.text as string | undefined) ?? ''
      prompt[node.id] = { class_type: TEXT_NODE, inputs: { text } }
    } else if (node.type === 'meshNode') {
      const filePath = node.data.params?.filePath as string | undefined
      const isWorkspacePath = isWorkspaceRelativePath(filePath)
      let meshPayload: { kind: 'workspace_path'; path: string } | null = null
      if (isWorkspacePath) {
        meshPayload = { kind: 'workspace_path', path: filePath }
      }
      if (!meshPayload) {
        return {
          ok: false,
          error: 'Load 3D Mesh needs a mesh that the server can read. Click "Upload local mesh…" or "From server…" on the node to pick one.',
        }
      }
      prompt[node.id] = { class_type: MESH_NODE, inputs: { mesh: meshPayload } }
    } else if (node.type === 'nodePackNode') {
      const ext = allNodePacks.find((item) => item.id === node.data.nodePackId)!
      const inputs = wireNodePackInputs(workflow, node.id, ext, allNodePacks)
      prompt[node.id] = {
        class_type: ext.id,
        inputs: {
          ...inputs,
          params: nodeParams(node, ext),
        },
      }
    } else if (node.type === 'outputNode' || node.type === 'previewNode') {
      // When a model/process node is the staged target, the transient sink
      // below is the only terminal needed for this execution branch.
      if (targetNode?.type === 'nodePackNode') continue
      // For a selected Output/Preview target, omit unrelated terminal branches
      // so their disconnected inputs cannot prevent a partial run.
      if (targetNode && node.id !== targetNode.id) continue
      if (!enabled(node)) continue
      const sourceId = incomingSource(workflow, node.id)
      if (!sourceId) {
        return { ok: false, error: `The "${sinkName(node.type)}" node has no incoming connection.` }
      }
      const source = workflow.nodes.find((candidate) => candidate.id === sourceId)
      if (!source) {
        return { ok: false, error: `The "${sinkName(node.type)}" node references a missing upstream node.` }
      }
      const sourceOutput = outputNameOf(source, allNodePacks)
      if (node.type === 'outputNode' && sourceOutput !== 'mesh') {
        return { ok: false, error: 'Output needs a mesh. Use Preview for image results.' }
      }
      if (node.type === 'previewNode' && sourceOutput !== 'mesh' && sourceOutput !== 'image') {
        return { ok: false, error: 'Preview needs an image or mesh result.' }
      }
      const sinkInput = node.type === 'previewNode' && sourceOutput === 'image' ? 'image' : 'mesh'
      prompt[node.id] = {
        class_type: node.type === 'outputNode' ? OUTPUT_NODE : PREVIEW_NODE,
        inputs: { [sinkInput]: [sourceId, sourceOutput] },
      }
      if (node.type === 'outputNode') outputNodeId = node.id
    }
  }

  // Partial runs may target a model/process node directly. The server's DAG
  // contract intentionally accepts only sink targets, so add a transient sink
  // to publish that node's result while keeping the user's graph unchanged.
  let executionTargetId = options.targetNodeId
  if (targetNode?.type === 'nodePackNode') {
    const targetOutput = outputNameOf(targetNode, allNodePacks)
    if (targetOutput !== 'mesh' && targetOutput !== 'image') {
      return { ok: false, error: 'The selected node does not produce a runnable image or mesh result.' }
    }
    // Keep the sink outside the editable graph and make its id unique even if
    // a user imported a workflow containing our reserved prefix.
    const targetSinkBase = `__run_target__${targetNode.id}`
    let targetSinkId = targetSinkBase
    let targetSinkSuffix = 1
    while (prompt[targetSinkId]) {
      targetSinkId = `${targetSinkBase}_${targetSinkSuffix}`
      targetSinkSuffix += 1
    }
    prompt[targetSinkId] = {
      class_type: targetOutput === 'image' ? 'polykit.image_output' : OUTPUT_NODE,
      inputs: { [targetOutput === 'image' ? 'image' : 'mesh']: [targetNode.id, targetOutput] },
    }
    executionTargetId = targetSinkId
    outputNodeId = targetSinkId
  }

  if (!Object.values(prompt).some((node) => (
    node.class_type === OUTPUT_NODE ||
    node.class_type === PREVIEW_NODE ||
    node.class_type === IMAGE_OUTPUT_NODE
  ))) {
    return { ok: false, error: 'The workflow could not be compiled for the server. Check the graph and try again.' }
  }

  return {
    ok: true,
    payload: {
      schema_version: 1,
      workflow_id: workflow.id,
      prompt,
      output_node_id: outputNodeId,
      ...(executionTargetId ? { target_node_ids: [executionTargetId] } : {}),
      collection: 'Workflows',
    },
  }
}

function incomingSource(workflow: Workflow, nodeId: string): string | undefined {
  return workflow.edges.find((edge) => edge.target === nodeId)?.source
}

function upstreamNodeIds(workflow: Workflow, targetNodeId: string): Set<string> {
  const ids = new Set<string>([targetNodeId])
  const pending = [targetNodeId]
  while (pending.length > 0) {
    const nodeId = pending.pop()!
    for (const edge of workflow.edges) {
      if (edge.target !== nodeId || ids.has(edge.source)) continue
      ids.add(edge.source)
      pending.push(edge.source)
    }
  }
  return ids
}

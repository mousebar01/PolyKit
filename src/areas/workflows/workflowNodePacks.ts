import { useAppStore } from '@shared/stores/appStore'
import type { ParamSchema } from '@shared/types/runtime.d'
import type { WorkflowNodePack } from './mockNodePacks'
import { localizeParamSchema } from './nodePackI18n'

type LocaleText = {
  name?: string
  description?: string
  inputLabels?: string[]
}

/** Server-side node schema returned by GET /node_types. */
export interface NodeDefinition {
  class_type:        string
  name:              string
  category:          'builtin' | 'model' | 'process'
  description:       string
  inputs:            string[]
  input_labels:      string[] | null
  outputs:           string[]
  batch_input?:      string | null
  params_schema:     ParamSchema[]
  builtin:           boolean
  i18n?:             Record<string, LocaleText>
  pack_i18n?:        Record<string, LocaleText>
  pack_name:         string | null
  pack_author:       string | null
  pack_id:           string | null
  node_id:           string | null
  pack_dir:          string | null
  entry:             string | null
}

type NodeType = WorkflowNodePack['input']

function toNodeType(value: string | undefined): NodeType {
  return (value as NodeType) ?? 'image'
}

/**
 * Map a server node definition to the editor's node-pack-node model. Locale
 * metadata changes presentation only; class_type, params, values and links stay
 * exactly as declared by the server.
 */
function toWorkflowNodePack(def: NodeDefinition): WorkflowNodePack | null {
  if (def.category !== 'model' && def.category !== 'process') return null
  const classParts = def.class_type.split('/')
  const extId  = def.pack_id ?? classParts[0] ?? def.class_type
  const nodeId = def.node_id ?? classParts.slice(1).join('/') ?? def.class_type
  const inputTypes = def.inputs.length > 0 ? def.inputs.map(toNodeType) : []

  return {
    id: def.class_type,
    nodePackId: extId,
    get nodePackName() {
      const language = useAppStore.getState().language
      return def.pack_i18n?.[language]?.name ?? def.pack_name ?? extId
    },
    nodePackAuthor: def.pack_author ?? '',
    nodeId,
    get name() {
      const language = useAppStore.getState().language
      return def.i18n?.[language]?.name ?? def.name
    },
    get description() {
      const language = useAppStore.getState().language
      return def.i18n?.[language]?.description
        ?? def.pack_i18n?.[language]?.description
        ?? def.description
    },
    input: inputTypes[0] ?? 'image',
    ...(inputTypes.length > 1 ? { inputs: inputTypes } : {}),
    get inputLabels() {
      const language = useAppStore.getState().language
      return def.i18n?.[language]?.inputLabels ?? def.input_labels ?? undefined
    },
    output: toNodeType(def.outputs[0]) === 'image' ? 'image' : (def.outputs[0] as WorkflowNodePack['output']) ?? 'mesh',
    ...(def.batch_input ? { batchInput: toNodeType(def.batch_input) } : {}),
    params: localizeParamSchema(def.params_schema),
    builtin: def.builtin,
    type: def.category === 'model' ? 'model' : 'process',
  }
}

/**
 * Fetch all executable node-pack nodes (model + process) from the headless
 * control plane. Throws when the API is unreachable so callers can fall back
 * to the server-provided node-pack list.
 */
export async function fetchWorkflowNodePacks(apiUrl: string): Promise<WorkflowNodePack[]> {
  const base = apiUrl.replace(/\/+$/, '')
  const response = await fetch(`${base}/node_types`)
  if (!response.ok) throw new Error(`GET /node_types failed: ${response.status}`)
  const data = await response.json() as { nodes: NodeDefinition[] }
  const nodePacks: WorkflowNodePack[] = []
  for (const def of data.nodes ?? []) {
    const mapped = toWorkflowNodePack(def)
    if (mapped) nodePacks.push(mapped)
  }
  return nodePacks
}

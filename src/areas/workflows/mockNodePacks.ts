import type { ModelNodePack, ProcessNodePack } from '@shared/stores/nodePacksStore'
import { useAppStore } from '@shared/stores/appStore'
export type { ParamSchema } from '@shared/types/runtime.d'
import type { ParamSchema } from '@shared/types/runtime.d'
import { localizeParamSchema } from './nodePackI18n'

type PackLocaleText = {
  name?: string
  description?: string
}

type NodeLocaleText = {
  name?: string
  description?: string
  inputLabels?: string[]
}

type PackWithI18n<T> = T & {
  i18n?: Record<string, PackLocaleText>
}

type NodeWithI18n<T> = T & {
  i18n?: Record<string, NodeLocaleText>
}

export interface WorkflowNodePack {
  id:              string   // "pack_id/node_id"
  nodePackId:     string   // "pack_id" (for IPC calls)
  nodePackName:   string   // display name of the parent extension
  nodePackAuthor: string   // author of the parent extension
  nodeId:          string   // "node_id"
  name:            string
  description:     string
  input:           'image' | 'text' | 'mesh' | 'audio'
  inputs?:         ('image' | 'text' | 'mesh' | 'audio')[]   // multi-input; overrides input when set
  inputLabels?:    string[]                                  // display labels per input slot
  output:          'image' | 'text' | 'mesh' | 'audio'
  params:          ParamSchema[]
  builtin:         boolean
  type:            'model' | 'process'
}

function applyParamDefaults(
  schema:   ParamSchema[],
  defaults: Record<string, number | string> | undefined,
): ParamSchema[] {
  if (!defaults || Object.keys(defaults).length === 0) return schema
  return schema.map((p) =>
    Object.prototype.hasOwnProperty.call(defaults, p.id)
      ? { ...p, default: defaults[p.id]! }
      : p,
  )
}

function prepareParams(
  schema: ParamSchema[],
  defaults: Record<string, number | string> | undefined,
): ParamSchema[] {
  // Apply machine-facing defaults first, then wrap only presentation fields.
  // Spreading an already-localized schema would materialize its getters and
  // freeze the language that happened to be active at that moment.
  return localizeParamSchema(applyParamDefaults(schema, defaults))
}

function pushNodePack<T extends ModelNodePack | ProcessNodePack>(
  result: WorkflowNodePack[],
  extValue: T,
  type: 'model' | 'process',
): void {
  const ext = extValue as PackWithI18n<T>

  for (const nodeValue of ext.nodes) {
    const node = nodeValue as NodeWithI18n<typeof nodeValue>
    result.push({
      id: `${ext.id}/${node.id}`,
      nodePackId: ext.id,
      get nodePackName() {
        const language = useAppStore.getState().language
        return ext.i18n?.[language]?.name ?? ext.name
      },
      nodePackAuthor: ext.author ?? '',
      nodeId: node.id,
      get name() {
        const language = useAppStore.getState().language
        return node.i18n?.[language]?.name ?? node.name
      },
      get description() {
        const language = useAppStore.getState().language
        return node.i18n?.[language]?.description
          ?? ext.i18n?.[language]?.description
          ?? ext.description
          ?? ''
      },
      input: node.input,
      inputs: node.inputs,
      get inputLabels() {
        const language = useAppStore.getState().language
        return node.i18n?.[language]?.inputLabels ?? node.inputLabels
      },
      output: node.output,
      params: prepareParams(node.paramsSchema as ParamSchema[], node.paramDefaults),
      builtin: ext.builtin,
      type,
    })
  }
}

export function buildAllWorkflowNodePacks(
  modelNodePacks:   ModelNodePack[],
  processNodePacks: ProcessNodePack[],
): WorkflowNodePack[] {
  const result: WorkflowNodePack[] = []

  for (const ext of processNodePacks) pushNodePack(result, ext, 'process')
  for (const ext of modelNodePacks) pushNodePack(result, ext, 'model')

  return result
}

export function getWorkflowNodePack(id: string, all: WorkflowNodePack[]): WorkflowNodePack | undefined {
  return all.find((e) => e.id === id)
}

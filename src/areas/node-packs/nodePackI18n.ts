import type { Language } from '@shared/stores/appStore'
import type { AnyNodePack, NodePackNode } from '@shared/types/runtime.d'

export type NodePackLocaleText = {
  name?: string
  description?: string
}

export type LocalizedNodePackNode = NodePackNode & {
  i18n?: Record<string, NodePackLocaleText>
}

export type LocalizedNodePack = AnyNodePack & {
  i18n?: Record<string, NodePackLocaleText>
  nodes: LocalizedNodePackNode[]
}

function asLocalizedPack(pack: AnyNodePack): LocalizedNodePack {
  return pack as LocalizedNodePack
}

function asLocalizedNode(node: NodePackNode): LocalizedNodePackNode {
  return node as LocalizedNodePackNode
}

export function localizedNodePackName(pack: AnyNodePack, language: Language): string {
  const localized = asLocalizedPack(pack)
  return localized.i18n?.[language]?.name ?? pack.name
}

export function localizedNodePackDescription(pack: AnyNodePack, language: Language): string | undefined {
  const localized = asLocalizedPack(pack)
  return localized.i18n?.[language]?.description ?? pack.description
}

export function localizedNodeName(node: NodePackNode, language: Language): string {
  const localized = asLocalizedNode(node)
  return localized.i18n?.[language]?.name ?? node.name
}

/**
 * Search both localized presentation text and source English text. This lets a
 * Chinese UI find a pack using either its translated label or the original
 * technical/project terminology without changing any machine identifiers.
 */
export function nodePackSearchText(pack: AnyNodePack, language: Language): string {
  const localizedName = localizedNodePackName(pack, language)
  const localizedDescription = localizedNodePackDescription(pack, language) ?? ''
  const nodeNames = pack.nodes.flatMap((node) => [node.name, localizedNodeName(node, language)])
  return [
    pack.name,
    localizedName,
    pack.description ?? '',
    localizedDescription,
    pack.author ?? '',
    ...nodeNames,
  ].join(' ').toLowerCase()
}

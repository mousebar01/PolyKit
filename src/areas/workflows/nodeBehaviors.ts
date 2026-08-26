import type { WFNode, WFEdge } from '@shared/types/runtime.d'

// ─── Node behaviors registry ──────────────────────────────────────────────────
//
// The workflow system is a linear DAG: sources → model/process node packs → a
// terminal scene output. The only behavior nodes need to participate in is
// "is this a scene output sink?" — everything else executes topologically.

export interface NodeBehavior {
  sceneOutput?: boolean
}

const BEHAVIORS: Record<string, NodeBehavior> = {
  outputNode: { sceneOutput: true },
}

export const isSceneOutput = (type: string | undefined): boolean => !!type && !!BEHAVIORS[type]?.sceneOutput

/**
 * Resolves a node's data source. With no passthrough/control nodes remaining,
 * a node's input always comes from the directly connected upstream node.
 */
export function resolveDataSource(sourceId: string): string {
  return sourceId
}

/**
 * True if any forward path from `sourceId` reaches a sceneOutput node.
 * Used to decide whether a produced mesh should be pushed to the viewer
 * immediately (it feeds the scene output).
 */
export function reachesSceneOutput(
  sourceId: string,
  edges: WFEdge[],
  nodeMap: Map<string, WFNode>,
): boolean {
  const stack = [sourceId]
  const seen  = new Set<string>()
  while (stack.length > 0) {
    const id = stack.pop()!
    if (seen.has(id)) continue
    seen.add(id)
    for (const e of edges) {
      if (e.source !== id) continue
      if (isSceneOutput(nodeMap.get(e.target)?.type)) return true
      stack.push(e.target)
    }
  }
  return false
}

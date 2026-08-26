import { getBezierPath, useReactFlow, useEdges } from '@xyflow/react'
import type { EdgeProps } from '@xyflow/react'
import { useNodePacksStore } from '@shared/stores/nodePacksStore'
import { buildAllWorkflowNodePacks } from '../mockNodePacks'

const HANDLE_COLOR: Record<string, string> = {
  audio: '#34d399',
  image: '#38bdf8',
  mesh:  '#c5f955',
  text:  '#fbbf24',
}

export default function WorkflowEdge({
  id, source, target,
  sourceX, sourceY, targetX, targetY,
  sourcePosition, targetPosition,
}: EdgeProps) {
  const { getNode } = useReactFlow()
  const edges       = useEdges()
  const { modelNodePacks, processNodePacks } = useNodePacksStore()
  const allNodePacks = buildAllWorkflowNodePacks(modelNodePacks, processNodePacks)

  const sourceNode = getNode(source)
  const targetNode = getNode(target)

  // Read targetHandle directly from edge store — reliable regardless of EdgeProps version
  const thisEdge    = edges.find((e) => e.id === id)
  const targetHandle = thisEdge?.targetHandle

  const sourceColor = sourceNode?.type === 'imageNode'
    ? HANDLE_COLOR.image
    : sourceNode?.type === 'textNode'
    ? HANDLE_COLOR.text
    : sourceNode?.type === 'meshNode'
    ? HANDLE_COLOR.mesh
    : (HANDLE_COLOR[allNodePacks.find((e) => e.id === sourceNode?.data?.nodePackId)?.output ?? ''] ?? '#52525b')

  // For multi-input nodes pick the color of the specific connected handle
  const targetExt = allNodePacks.find((e) => e.id === targetNode?.data?.nodePackId)
  const targetInputType = (() => {
    if (targetExt?.inputs && targetExt.inputs.length > 1 && targetHandle) {
      const idx = parseInt(targetHandle.replace('input-', ''), 10)
      return targetExt.inputs[isNaN(idx) ? 0 : idx] ?? targetExt.input
    }
    return targetExt?.input
  })()

  const targetColor = targetNode?.type === 'outputNode'
    ? HANDLE_COLOR.mesh
    : targetNode?.type === 'previewNode'
    ? HANDLE_COLOR.image
    : (HANDLE_COLOR[targetInputType ?? ''] ?? '#52525b')

  const [edgePath] = getBezierPath({ sourceX, sourceY, sourcePosition, targetX, targetY, targetPosition })
  const gradientId = `wf-edge-${id}`

  return (
    <>
      <defs>
        <linearGradient id={gradientId} gradientUnits="userSpaceOnUse" x1={sourceX} y1={sourceY} x2={targetX} y2={targetY}>
          <stop offset="0%"   stopColor={sourceColor} />
          <stop offset="100%" stopColor={targetColor} />
        </linearGradient>
      </defs>
      <path
        d={edgePath}
        fill="none"
        style={{ stroke: `url(#${gradientId})`, strokeWidth: 2.5 }}
        className="react-flow__edge-path"
      />
    </>
  )
}

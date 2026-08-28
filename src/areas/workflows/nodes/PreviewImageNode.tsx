import { ImageIcon, Play } from 'lucide-react'
import { Handle, NodeToolbar, Position, useReactFlow } from '@xyflow/react'

import { useWorkflowRunStore } from '../workflowRunStore'
import { useWorkflowNodeExecution } from '../workflowNodeExecutionContext'
import { useI18n } from '@shared/i18n'
import { Button } from '@shared/components/ui'
import BaseNode from './BaseNode'

const INPUT_COLOR = '#38bdf8'

/**
 * Preview node for multi-view image outputs (e.g. MV-Adapter Generate Views).
 *
 * Expects a vertical strip PNG where N views are stacked top→bottom.
 * Displays them in a 2×3 grid using CSS background-position cropping.
 */
export default function PreviewImageNode({ id, selected }: { id: string; selected?: boolean }) {
  const { t } = useI18n()
  const { isRunning, runToHere } = useWorkflowNodeExecution()
  const nodeImageOutputs = useWorkflowRunStore((state) => state.nodeImageOutputs)
  const { getEdges } = useReactFlow()

  const incomingEdge = getEdges().find((edge) => edge.target === id)
  const imageUrl = incomingEdge ? nodeImageOutputs[incomingEdge.source] : undefined

  return (
    <>
      <NodeToolbar
        isVisible={Boolean(selected) && !isRunning}
        position={Position.Top}
        offset={6}
        className="flex items-center rounded-md border border-border bg-card p-1"
      >
        <Button
          type="button"
          variant="default"
          size="icon"
          className="nodrag h-7 w-7"
          onClick={(event) => { event.stopPropagation(); runToHere(id) }}
          title={t('workflows.runToHereHint')}
          aria-label={t('workflows.runToHereHint')}
        >
          <Play className="size-3.5" fill="currentColor" />
        </Button>
      </NodeToolbar>
    <BaseNode
      id={id}
      selected={selected}
      title="Preview Views"
      minWidth={200}
      icon={<ImageIcon className="h-3 w-3 text-sky-400" />}
      subheader={
        <div className="flex items-center gap-1.5 px-3 py-2">
          <span className="inline-flex items-center rounded border border-sky-500/30 bg-sky-500/10 px-1.5 py-0.5 text-[9px] font-medium text-sky-400">image</span>
          <span className="text-[9px] text-muted-foreground">→ preview</span>
        </div>
      }
      handles={
        <Handle
          type="target"
          position={Position.Left}
          style={{ background: INPUT_COLOR, width: 14, height: 14, border: '2.5px solid #18181b' }}
        />
      }
    >
      <div className="px-2 pb-2 pt-1">
        {imageUrl ? (
          <div
            className="nodrag grid gap-0.5 overflow-hidden rounded-md"
            style={{ gridTemplateColumns: 'repeat(3, 1fr)' }}
          >
            {[0, 1, 2, 3, 4, 5].map((index) => (
              <div
                key={index}
                style={{
                  aspectRatio: '1',
                  backgroundImage: `url(${imageUrl})`,
                  backgroundSize: '100% 600%',
                  backgroundPosition: `0 ${index * 20}%`,
                  backgroundRepeat: 'no-repeat',
                  borderRadius: '2px',
                }}
              />
            ))}
          </div>
        ) : (
          <p className="py-2 text-center text-[10px] italic text-muted-foreground">
            Connect a multi-view image to preview.
          </p>
        )}
      </div>
    </BaseNode>
    </>
  )
}

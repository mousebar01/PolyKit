import { ImageIcon, Play } from 'lucide-react'
import { Handle, Position, useReactFlow } from '@xyflow/react'

import { useWorkflowRunStore } from '../workflowRunStore'
import { useWorkflowNodeExecution } from '../workflowNodeExecutionContext'
import { useI18n } from '@shared/i18n'
import { Button } from '@shared/components/ui'
import BaseNode from './BaseNode'

const INPUT_COLOR = '#38bdf8'

/** Preview node for the image artifact produced by an upstream workflow node. */
export default function PreviewImageNode({ id, selected }: { id: string; selected?: boolean }) {
  const { t } = useI18n()
  const { isRunning, runToHere } = useWorkflowNodeExecution()
  const nodeImageOutputs = useWorkflowRunStore((state) => state.nodeImageOutputs)
  const { getEdges } = useReactFlow()

  const incomingEdge = getEdges().find((edge) => edge.target === id)
  const imageUrl = incomingEdge ? nodeImageOutputs[incomingEdge.source] : undefined

  return (
    <BaseNode
      id={id}
      selected={selected}
      title="Preview"
      minWidth={200}
      icon={<ImageIcon className="h-3 w-3 text-sky-400" />}
      actions={(
        <Button
          type="button"
          variant="ghost"
          size="icon"
          className="nodrag mt-0.5 h-6 w-6 shrink-0 text-primary hover:bg-primary/10 hover:text-primary"
          onClick={(event) => { event.stopPropagation(); runToHere(id) }}
          disabled={isRunning}
          title={t('workflows.runToHereHint')}
          aria-label={t('workflows.runToHereHint')}
        >
          <Play className="size-3" fill="currentColor" />
        </Button>
      )}
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
          <div className="nodrag overflow-hidden rounded-md bg-muted/20">
            <img
              src={imageUrl}
              alt=""
              draggable={false}
              className="block max-h-64 w-full object-contain"
            />
          </div>
        ) : (
          <p className="py-2 text-center text-[10px] italic text-muted-foreground">
            Connect an image to preview.
          </p>
        )}
      </div>
    </BaseNode>
  )
}

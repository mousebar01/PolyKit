import { useCallback } from 'react'
import { ArrowRight, Box, Play } from 'lucide-react'
import { Handle, NodeToolbar, Position } from '@xyflow/react'

import { Button } from '@shared/components/ui'
import { useAppStore } from '@shared/stores/appStore'
import { useNavStore } from '@shared/stores/navStore'
import { useI18n } from '@shared/i18n'
import type { WFNodeData } from '@shared/types/runtime.d'
import BaseNode from './BaseNode'
import { useWorkflowNodeExecution } from '../workflowNodeExecutionContext'

const INPUT_COLOR = '#5d94d9'

export default function OutputNode({ id, data, selected }: { id: string; data: WFNodeData; selected?: boolean }) {
  const { t } = useI18n()
  const { navigate } = useNavStore()
  const { isRunning, runToHere } = useWorkflowNodeExecution()
  const setCurrentJob = useAppStore((state) => state.setCurrentJob)
  const outputUrl = data.params.outputUrl as string | undefined

  const viewIn3D = useCallback(() => {
    if (!outputUrl) return
    setCurrentJob({ id: 'workflow-output', imageFile: '', status: 'done', progress: 100, outputUrl, createdAt: Date.now() })
    navigate('assets')
  }, [outputUrl, setCurrentJob, navigate])

  return (
    <>
      <NodeToolbar
        isVisible={Boolean(selected) && data.enabled !== false && !isRunning}
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
      title="Output"
      minWidth={160}
      icon={<Box className="h-3 w-3 text-primary" />}
      subheader={
        <div className="flex items-center gap-1.5 px-3 py-2">
          <span className="inline-flex items-center rounded border border-primary/30 bg-primary/10 px-1.5 py-0.5 text-[9px] font-medium text-primary">mesh</span>
          <span className="text-[9px] text-muted-foreground">→ output</span>
        </div>
      }
      handles={
        <Handle type="target" position={Position.Left}
          style={{ background: INPUT_COLOR, width: 14, height: 14, border: '2.5px solid #18181b' }} />
      }
    >
      <div className="px-3 pb-3 pt-2.5">
        {outputUrl ? (
          <div className="flex flex-col gap-2">
            <div className="flex items-center gap-1.5">
              <div className="h-1.5 w-1.5 shrink-0 rounded-full bg-sky-500" />
              <span className="text-[10px] text-sky-400">Mesh ready</span>
            </div>
            <Button
              type="button"
              size="sm"
              onClick={viewIn3D}
              className="nodrag w-full gap-1.5 text-[10px]"
            >
              View in 3D
              <ArrowRight className="h-3 w-3" />
            </Button>
          </div>
        ) : (
          <p className="text-[10px] italic text-muted-foreground">Connect a mesh to produce the workflow output.</p>
        )}
      </div>
    </BaseNode>
    </>
  )
}

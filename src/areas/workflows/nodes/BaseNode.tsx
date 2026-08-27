import { useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { NodeResizer, useReactFlow } from '@xyflow/react'
import { ChevronDown, Eye, EyeOff, LoaderCircle, X } from 'lucide-react'

import { Badge, Button, Switch } from '@shared/components/ui'

const RESIZER_HANDLE_STYLE = { background: 'transparent', border: 'none', width: 12, height: 12 }

export interface BaseNodeProps {
  id: string
  selected?: boolean
  running?: boolean
  title: string
  icon?: ReactNode
  badge?: string
  enabled?: boolean
  showInGenerate?: boolean
  deletable?: boolean
  collapsible?: boolean
  defaultExpanded?: boolean
  subheader?: ReactNode
  handles?: ReactNode
  minWidth?: number
  minHeight?: number
  autoHeight?: boolean
  children?: ReactNode
}

export default function BaseNode({
  id, selected, running,
  title, icon, badge,
  enabled, showInGenerate,
  deletable = true,
  collapsible = false,
  defaultExpanded = true,
  subheader, handles,
  minWidth = 180,
  minHeight = 60,
  autoHeight = false,
  children,
}: BaseNodeProps) {
  const { updateNodeData, deleteElements } = useReactFlow()
  const [expanded, setExpanded] = useState(defaultExpanded)
  const rootRef = useRef<HTMLDivElement>(null)
  const isDisabled = enabled === false

  return (
    <div
      ref={rootRef}
      style={autoHeight ? { width: '100%' } : { width: '100%', height: '100%' }}
      className={`relative flex flex-col rounded-lg border bg-card/95 text-card-foreground backdrop-blur-sm transition-colors ${running ? 'border-primary animate-pulse' : selected ? 'border-primary/70' : isDisabled ? 'border-divider opacity-50' : 'border-divider'}`}
    >
      <NodeResizer
        minWidth={minWidth}
        minHeight={autoHeight ? 0 : minHeight}
        lineStyle={{ borderColor: 'transparent' }}
        handleStyle={autoHeight ? { display: 'none' } : RESIZER_HANDLE_STYLE}
      />

      {handles}

      <div className="flex shrink-0 items-start gap-2 px-3 pb-2.5 pt-3">
        {running && (
          <div className="mt-0.5 shrink-0 text-primary">
            <LoaderCircle className="h-3 w-3 animate-spin" />
          </div>
        )}
        {!running && icon && <div className="mt-0.5 shrink-0">{icon}</div>}

        <div className="min-w-0 flex-1">
          <p className="truncate text-[11px] font-semibold leading-tight text-foreground">{title}</p>
          {badge && (
            <Badge variant="outline" className="mt-0.5 h-4 px-1.5 py-0 text-[8px] uppercase tracking-wide text-muted-foreground">
              {badge}
            </Badge>
          )}
        </div>

        {showInGenerate !== undefined && (
          <Button
            type="button"
            variant="ghost"
            size="icon"
            className={`nodrag mt-0.5 h-5 w-5 shrink-0 ${showInGenerate ? 'text-primary' : 'text-muted-foreground'}`}
            onClick={() => updateNodeData(id, { showInGenerate: !showInGenerate })}
            title={showInGenerate ? 'Visible in Generate' : 'Hidden from Generate'}
            aria-label={showInGenerate ? 'Hide from Generate' : 'Show in Generate'}
            aria-pressed={showInGenerate}
          >
            {showInGenerate ? <Eye className="h-3 w-3" /> : <EyeOff className="h-3 w-3" />}
          </Button>
        )}

        {enabled !== undefined && (
          <Switch
            checked={enabled}
            onCheckedChange={(checked) => updateNodeData(id, { enabled: checked })}
            className="nodrag mt-0.5 shrink-0"
            title={enabled ? 'Disable' : 'Enable'}
            aria-label={`${enabled ? 'Disable' : 'Enable'} ${title}`}
          />
        )}

        {collapsible && (
          <Button
            type="button"
            variant="ghost"
            size="icon"
            className="nodrag mt-0.5 h-5 w-5 shrink-0 text-muted-foreground"
            onClick={() => setExpanded((value) => !value)}
            title={expanded ? 'Collapse node' : 'Expand node'}
            aria-label={expanded ? 'Collapse node' : 'Expand node'}
            aria-expanded={expanded}
          >
            <ChevronDown className={`h-3 w-3 transition-transform ${expanded ? 'rotate-180' : ''}`} />
          </Button>
        )}

        {deletable && (
          <Button
            type="button"
            variant="ghost"
            size="icon"
            className="nodrag mt-0.5 h-5 w-5 shrink-0 text-muted-foreground hover:bg-destructive/10 hover:text-destructive"
            onClick={() => deleteElements({ nodes: [{ id }] })}
            title={`Delete ${title}`}
            aria-label={`Delete ${title}`}
          >
            <X className="h-3 w-3" strokeWidth={2.5} />
          </Button>
        )}
      </div>

      {subheader && <div className="shrink-0 border-t border-divider">{subheader}</div>}

      {children && (!collapsible || expanded) && (
        <div className="flex min-h-0 flex-1 flex-col border-t border-divider">
          {children}
        </div>
      )}

    </div>
  )
}

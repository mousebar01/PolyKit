import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { AlignLeft } from 'lucide-react'
import { Handle, Position, useReactFlow } from '@xyflow/react'

import { Textarea } from '@shared/components/ui'
import type { WFNodeData } from '@shared/types/runtime.d'
import BaseNode from './BaseNode'

const OUTPUT_COLOR = '#fbbf24'
const MIN_TEXTAREA_HEIGHT = 96
const MAX_TEXTAREA_HEIGHT = 280

export default function TextNode({ id, data, selected }: { id: string; data: WFNodeData; selected?: boolean }) {
  const { updateNodeData, setNodes } = useReactFlow()
  const ioRowRef = useRef<HTMLDivElement>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const manualHeightRef = useRef(false)
  const [handleTop, setHandleTop] = useState('50%')

  useLayoutEffect(() => {
    if (ioRowRef.current) {
      const center = ioRowRef.current.offsetTop + ioRowRef.current.offsetHeight / 2
      setHandleTop(`${center}px`)
    }
  }, [])

  useEffect(() => {
    setNodes((nodes) => nodes.map((node) => node.id === id ? { ...node, height: undefined } : node))
  }, [id, setNodes])

  const text = (data.params.text as string | undefined) ?? ''

  useLayoutEffect(() => {
    const textarea = textareaRef.current
    if (!textarea) return
    if (manualHeightRef.current) {
      textarea.style.overflowY = textarea.scrollHeight > textarea.clientHeight ? 'auto' : 'hidden'
      return
    }
    textarea.style.height = '0px'
    const contentHeight = textarea.scrollHeight
    textarea.style.height = `${Math.min(Math.max(contentHeight, MIN_TEXTAREA_HEIGHT), MAX_TEXTAREA_HEIGHT)}px`
    textarea.style.overflowY = contentHeight > MAX_TEXTAREA_HEIGHT ? 'auto' : 'hidden'
  }, [text])

  return (
    <BaseNode
      id={id}
      selected={selected}
      title="Text"
      showInGenerate={data.showInGenerate ?? false}
      minWidth={180}
      autoHeight
      icon={<AlignLeft className="h-3 w-3 text-amber-400" />}
      subheader={
        <div ref={ioRowRef} className="flex items-center justify-end px-3 py-2">
          <span className="inline-flex items-center rounded border border-amber-500/30 bg-amber-500/10 px-1.5 py-0.5 text-[9px] font-medium text-amber-400">text</span>
        </div>
      }
      handles={
        <Handle
          type="source"
          position={Position.Right}
          style={{ background: OUTPUT_COLOR, width: 14, height: 14, border: '2.5px solid #18181b', top: handleTop }}
        />
      }
    >
      <div className="px-3 pb-3 pt-2.5">
        <Textarea
          ref={textareaRef}
          value={text}
          onChange={(event) => updateNodeData(id, { params: { ...data.params, text: event.target.value } })}
          onMouseDown={(event) => {
            const rect = event.currentTarget.getBoundingClientRect()
            if (rect.right - event.clientX < 18 && rect.bottom - event.clientY < 18) manualHeightRef.current = true
          }}
          placeholder="Enter text…"
          rows={1}
          aria-label="Text input"
          className="nodrag nowheel min-h-0 max-h-[520px] resize-y overflow-y-auto px-2.5 py-2 text-[11px] leading-relaxed"
        />
      </div>
    </BaseNode>
  )
}

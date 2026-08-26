import { useEffect, useRef } from 'react'
import { useReactFlow } from '@xyflow/react'

import { Textarea } from '@shared/components/ui'
import type { WFNodeData } from '@shared/types/runtime.d'

// ComfyUI-style Note: a free-form annotation box on the canvas. It has no
// handles, never executes, and is ignored by preflight/compilation/runners.
export default function NoteNode({ id, data, selected }: { id: string; data: WFNodeData; selected?: boolean }): JSX.Element {
  const { updateNodeData, setNodes } = useReactFlow()
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const text = (data.params.text as string | undefined) ?? ''

  useEffect(() => {
    setNodes((nodes) => nodes.map((node) => (node.id === id ? { ...node, height: undefined } : node)))
  }, [id, setNodes])

  useEffect(() => {
    const textarea = textareaRef.current
    if (!textarea) return
    textarea.style.height = 'auto'
    textarea.style.height = `${textarea.scrollHeight}px`
  }, [text])

  return (
    <div
      className={`nodrag min-w-[180px] max-w-[300px] rounded-lg border p-3 transition-shadow ${selected
        ? 'border-amber-400/50 bg-amber-400/10 ring-2 ring-amber-400/15'
        : 'border-amber-500/25 bg-amber-400/[0.07] hover:border-amber-500/40'}`}
    >
      <Textarea
        ref={textareaRef}
        value={text}
        onChange={(event) => updateNodeData(id, { params: { ...data.params, text: event.target.value } })}
        placeholder="Write a note…"
        rows={3}
        aria-label="Workflow note"
        className="min-h-0 resize-none overflow-hidden border-0 bg-transparent p-0 text-[11px] leading-relaxed text-amber-100/90 shadow-none placeholder:text-amber-200/30 focus-visible:ring-0 focus-visible:ring-offset-0"
      />
    </div>
  )
}

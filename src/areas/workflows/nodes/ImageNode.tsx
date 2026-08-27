import { useCallback, useLayoutEffect, useRef, useState } from 'react'
import { FolderOpen, ImageIcon } from 'lucide-react'
import { Handle, Position, useReactFlow } from '@xyflow/react'

import { Button } from '@shared/components/ui'
import type { WFNodeData } from '@shared/types/runtime.d'
import BaseNode from './BaseNode'

const OUTPUT_COLOR = '#38bdf8'

function mimeFromPath(path: string): string {
  const ext = path.split('.').pop()?.toLowerCase() ?? ''
  if (ext === 'jpg' || ext === 'jpeg') return 'image/jpeg'
  if (ext === 'webp') return 'image/webp'
  return 'image/png'
}

export default function ImageNode({ id, data, selected }: { id: string; data: WFNodeData; selected?: boolean }) {
  const { updateNodeData } = useReactFlow()
  const ioRowRef = useRef<HTMLDivElement>(null)
  const [handleTop, setHandleTop] = useState('50%')

  useLayoutEffect(() => {
    if (ioRowRef.current) {
      const center = ioRowRef.current.offsetTop + ioRowRef.current.offsetHeight / 2
      setHandleTop(`${center}px`)
    }
  }, [])

  const filePath = data.params.filePath as string | undefined
  const preview = data.params.preview as string | undefined

  const browse = useCallback(async () => {
    const path = await window.polykit.fs.selectImage()
    if (!path) return
    const base64 = await window.polykit.fs.readFileBase64(path)
    const src = `data:${mimeFromPath(path)};base64,${base64}`
    // Persist the image on the server so it survives reloads.
    const uploaded = await window.polykit.fs.uploadImage(path)
    updateNodeData(id, { params: { ...data.params, filePath: uploaded ?? path, preview: src } })
  }, [id, data.params, updateNodeData])

  return (
    <BaseNode
      id={id}
      selected={selected}
      title="Image"
      showInGenerate={data.showInGenerate ?? false}
      minWidth={160}
      icon={<ImageIcon className="h-3 w-3 text-sky-400" />}
      subheader={
        <div ref={ioRowRef} className="flex items-center justify-end px-3 py-2">
          <span className="inline-flex items-center rounded border border-sky-500/30 bg-sky-500/10 px-1.5 py-0.5 text-[9px] font-medium text-sky-400">image</span>
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
      {preview ? (
        <Button
          type="button"
          variant="ghost"
          onClick={browse}
          className="nodrag group relative h-auto flex-1 overflow-hidden rounded-b-md rounded-t-none p-0"
          aria-label="Change image"
        >
          <img src={preview} alt={filePath?.split(/[\\/]/).pop() ?? ''} className="h-full w-full object-cover" />
          <span className="absolute inset-0 flex items-center justify-center bg-black/0 transition-colors group-hover:bg-black/40">
            <span className="text-[10px] font-medium text-white opacity-0 transition-opacity group-hover:opacity-100">Change…</span>
          </span>
        </Button>
      ) : (
        <div className="px-3 py-3">
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={browse}
            className="nodrag w-full justify-start gap-2 border-dashed text-muted-foreground hover:text-foreground"
          >
            <FolderOpen className="h-3.5 w-3.5" />
            Browse…
          </Button>
        </div>
      )}
    </BaseNode>
  )
}

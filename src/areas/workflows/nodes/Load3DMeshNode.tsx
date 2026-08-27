import { useCallback, useLayoutEffect, useRef, useState } from 'react'
import { Box, FolderOpen, Search } from 'lucide-react'
import { Handle, Position, useReactFlow } from '@xyflow/react'

import { Button } from '@shared/components/ui'
import { useAppStore } from '@shared/stores/appStore'
import type { WFNodeData } from '@shared/types/runtime.d'
import ServerFileBrowser from '../components/ServerFileBrowser'
import BaseNode from './BaseNode'

const OUTPUT_COLOR = '#5d94d9'

interface ServerMesh {
  workspacePath: string
  name: string
}

export default function Load3DMeshNode({ id, data, selected }: { id: string; data: WFNodeData; selected?: boolean }) {
  const { updateNodeData } = useReactFlow()
  const ioRowRef = useRef<HTMLDivElement>(null)
  const [handleTop, setHandleTop] = useState('50%')
  const [browserOpen, setBrowserOpen] = useState(false)

  useLayoutEffect(() => {
    if (ioRowRef.current) {
      const center = ioRowRef.current.offsetTop + ioRowRef.current.offsetHeight / 2
      setHandleTop(`${center}px`)
    }
  }, [])

  const apiUrl = useAppStore((state) => state.apiUrl)
  const fileName = data.params.fileName as string | undefined

  const browseLocal = useCallback(async () => {
    const path = await window.polykit.fs.selectMeshFile()
    if (!path) return
    const name = path.split(/[\\/]/).pop() ?? path
    const uploaded = await window.polykit.fs.uploadMesh(path)
    updateNodeData(id, { params: { ...data.params, source: 'file', filePath: uploaded ?? path, fileName: name } })
  }, [id, data.params, updateNodeData])

  const browseServer = useCallback(() => setBrowserOpen(true), [])

  const pickServerMesh = useCallback((mesh: ServerMesh) => {
    const name = mesh.workspacePath.split('/').pop() ?? mesh.workspacePath
    updateNodeData(id, { params: { ...data.params, source: 'file', filePath: mesh.workspacePath, fileName: name } })
    setBrowserOpen(false)
  }, [id, data.params, updateNodeData])

  return (
    <BaseNode
      id={id}
      selected={selected}
      title="Load 3D Mesh"
      showInGenerate={data.showInGenerate ?? false}
      minWidth={180}
      icon={<Box className="h-3 w-3 text-primary" />}
      subheader={
        <div ref={ioRowRef} className="flex items-center justify-end px-3 py-2">
          <span className="inline-flex items-center rounded border border-primary/30 bg-primary/10 px-1.5 py-0.5 text-[9px] font-medium text-primary">mesh</span>
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
      <div className="flex flex-col gap-2 px-3 py-2.5">
        <div className="flex flex-col gap-1.5">
          {fileName ? (
            <Button
              type="button"
              variant="secondary"
              size="sm"
              onClick={browseLocal}
              className="nodrag group w-full justify-start gap-2 px-2.5"
            >
              <Box className="h-3.5 w-3.5 shrink-0 text-primary" />
              <span className="min-w-0 flex-1 truncate text-left text-[10px]">{fileName}</span>
              <span className="shrink-0 text-[9px] font-normal text-muted-foreground group-hover:text-foreground">Change…</span>
            </Button>
          ) : (
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={browseLocal}
              className="nodrag w-full justify-start gap-2 border-dashed text-muted-foreground hover:text-foreground"
            >
              <FolderOpen className="h-3.5 w-3.5" />
              Upload local mesh…
            </Button>
          )}

          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={browseServer}
            className="nodrag w-full justify-start gap-2 border-dashed text-muted-foreground hover:text-foreground"
          >
            <Search className="h-3.5 w-3.5" />
            From server…
          </Button>
        </div>
      </div>
      {browserOpen && (
        <ServerFileBrowser
          thumbnailBase={apiUrl}
          onClose={() => setBrowserOpen(false)}
          onSelect={pickServerMesh}
        />
      )}
    </BaseNode>
  )
}

import { useEffect, useMemo, useState } from 'react'
import { Box, Folder } from 'lucide-react'

import {
  Button,
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@shared/components/ui'

/**
 * File-manager-style picker for meshes already on the server workspace.
 *
 * Instead of a flat list, it behaves like a file explorer: folder navigation
 * with breadcrumbs, and a grid of folders + mesh cards with live thumbnails.
 */
interface ServerMeshEntry {
  workspacePath: string
  name: string
  thumbnail?: string
  previewKind?: string
  capability?: string
}

interface ServerFileBrowserProps {
  thumbnailBase: string
  onClose: () => void
  onSelect: (entry: ServerMeshEntry) => void
}

function dirname(path: string): string {
  const index = path.lastIndexOf('/')
  return index < 0 ? '' : path.slice(0, index)
}

export default function ServerFileBrowser({ thumbnailBase, onClose, onSelect }: ServerFileBrowserProps): JSX.Element {
  const [entries, setEntries] = useState<ServerMeshEntry[] | null>(null)
  const [loading, setLoading] = useState(false)
  const [currentDir, setCurrentDir] = useState('')
  const [error, setError] = useState(false)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(false)
    setEntries(null)
    setCurrentDir('')
    window.polykit.fs.listServerMeshes()
      .then((list) => { if (!cancelled) setEntries(list) })
      .catch(() => { if (!cancelled) setError(true) })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [])

  const folders = useMemo(() => {
    if (!entries) return []
    const prefix = currentDir ? `${currentDir}/` : ''
    const names = new Set<string>()
    for (const entry of entries) {
      if (!entry.workspacePath.startsWith(prefix)) continue
      const rest = entry.workspacePath.slice(prefix.length)
      const segment = rest.split('/')[0]
      if (rest.includes('/')) names.add(segment)
    }
    return [...names].sort((a, b) => a.localeCompare(b))
  }, [entries, currentDir])

  const files = useMemo(() => {
    if (!entries) return []
    return entries
      .filter((entry) => dirname(entry.workspacePath) === currentDir)
      .sort((a, b) => a.name.localeCompare(b.name))
  }, [entries, currentDir])

  const crumbs = currentDir ? currentDir.split('/') : []

  const goTo = (depth: number) => {
    setCurrentDir(depth === 0 ? '' : crumbs.slice(0, depth).join('/'))
  }

  return (
    <Dialog open onOpenChange={(open) => { if (!open) onClose() }}>
      <DialogContent className="nodrag flex max-h-[440px] w-[calc(100vw-3rem)] max-w-[600px] flex-col gap-0 overflow-hidden p-0">
        <DialogHeader className="shrink-0 border-b border-border px-4 py-3 pr-12">
          <div className="flex items-center gap-2">
            <Folder className="h-4 w-4 text-primary" aria-hidden="true" />
            <DialogTitle className="text-xs">Server workspace</DialogTitle>
          </div>
          <DialogDescription className="sr-only">Choose a mesh from the server workspace.</DialogDescription>
        </DialogHeader>

        <div className="flex shrink-0 items-center gap-1 border-b border-border px-4 py-2 text-[11px]" aria-label="Workspace path">
          <Button
            type="button"
            variant={currentDir === '' ? 'secondary' : 'ghost'}
            size="sm"
            className="h-7 px-2 text-[11px]"
            onClick={() => goTo(0)}
          >
            All
          </Button>
          {crumbs.map((part, index) => (
            <span key={`${part}-${index}`} className="flex items-center gap-1">
              <span className="text-muted-foreground" aria-hidden="true">/</span>
              <Button
                type="button"
                variant={index === crumbs.length - 1 ? 'secondary' : 'ghost'}
                size="sm"
                className="h-7 px-2 text-[11px]"
                onClick={() => goTo(index + 1)}
              >
                {part}
              </Button>
            </span>
          ))}
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto p-3">
          {loading && (
            <div className="flex h-full items-center justify-center py-10 text-[11px] text-muted-foreground" role="status">
              Loading server workspace…
            </div>
          )}
          {error && !loading && (
            <div className="flex h-full items-center justify-center py-10 text-[11px] text-destructive" role="alert">
              Could not reach the server.
            </div>
          )}
          {!loading && !error && entries && entries.length === 0 && (
            <div className="flex h-full items-center justify-center py-10 text-[11px] text-muted-foreground" role="status">
              No meshes on the server yet — upload one or run a workflow.
            </div>
          )}
          {!loading && !error && entries && entries.length > 0 && (
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
              {folders.map((folder) => (
                <Button
                  key={folder}
                  type="button"
                  variant="outline"
                  onClick={() => setCurrentDir(currentDir ? `${currentDir}/${folder}` : folder)}
                  className="h-auto justify-start gap-2 px-3 py-2.5 text-left"
                >
                  <Folder className="h-[18px] w-[18px] shrink-0 text-amber-500/80" aria-hidden="true" />
                  <span className="truncate text-[11px]">{folder}</span>
                </Button>
              ))}
              {files.map((entry) => {
                const thumbUrl = entry.thumbnail ? `${thumbnailBase}${entry.thumbnail}` : undefined
                return (
                  <Button
                    key={entry.workspacePath}
                    type="button"
                    variant="outline"
                    onClick={() => onSelect(entry)}
                    title={entry.workspacePath}
                    className="group h-auto flex-col items-stretch gap-1.5 p-2 text-left hover:border-primary/40"
                  >
                    <span className="relative flex h-14 items-center justify-center overflow-hidden rounded-md border border-border bg-muted/40">
                      <Box className="h-[18px] w-[18px] text-muted-foreground" strokeWidth={1.5} aria-hidden="true" />
                      {thumbUrl && (
                        <img
                          src={thumbUrl}
                          alt=""
                          loading="lazy"
                          onError={(event) => { event.currentTarget.style.display = 'none' }}
                          className="absolute inset-0 h-full w-full object-cover"
                        />
                      )}
                    </span>
                    <span className="truncate text-[10px] font-normal">{entry.name}</span>
                  </Button>
                )
              })}
            </div>
          )}
        </div>

        <DialogFooter className="shrink-0 items-center justify-between border-t border-border px-4 py-2.5">
          <span className="mr-auto text-[10px] text-muted-foreground">{entries ? `${entries.length} mesh(es)` : ''}</span>
          <Button type="button" variant="outline" size="sm" onClick={onClose}>Cancel</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

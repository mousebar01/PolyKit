import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { FolderOpen, ImageIcon, Images, LoaderCircle, RefreshCw, Search } from 'lucide-react'
import { Handle, Position, useReactFlow } from '@xyflow/react'

import { getDefaultAssetLibraryService } from '@areas/assets/assetLibraryService'
import type { ProjectedAssetLibraryEntry } from '@areas/assets/assetLibraryProjection'
import { Button, Input, Popover, PopoverContent, PopoverTrigger } from '@shared/components/ui'
import { useI18n } from '@shared/i18n'
import { useAppStore } from '@shared/stores/appStore'
import type { WFNodeData } from '@shared/types/runtime.d'
import BaseNode from './BaseNode'

const OUTPUT_COLOR = '#38bdf8'

function mimeFromPath(path: string): string {
  const ext = path.split('.').pop()?.toLowerCase() ?? ''
  if (ext === 'jpg' || ext === 'jpeg') return 'image/jpeg'
  if (ext === 'webp') return 'image/webp'
  return 'image/png'
}

function withApiBase(url: string, apiUrl: string): string {
  if (/^(?:data:|blob:|https?:\/\/)/i.test(url) || !apiUrl) return url
  return `${apiUrl.replace(/\/+$/, '')}/${url.replace(/^\/+/, '')}`
}

function imagePreviewUrl(entry: ProjectedAssetLibraryEntry, apiUrl: string): string {
  const url = entry.preview ?? entry.thumbnail
  if (url) return withApiBase(url, apiUrl)
  return withApiBase(`/workspace/${entry.workspacePath}`, apiUrl)
}

export default function ImageNode({ id, data, selected }: { id: string; data: WFNodeData; selected?: boolean }) {
  const { updateNodeData } = useReactFlow()
  const { t } = useI18n()
  const apiUrl = useAppStore((state) => state.apiUrl)
  const ioRowRef = useRef<HTMLDivElement>(null)
  const [handleTop, setHandleTop] = useState('50%')
  const [pickerOpen, setPickerOpen] = useState(false)
  const [pickerTab, setPickerTab] = useState<'local' | 'library'>('local')
  const [localLoading, setLocalLoading] = useState(false)
  const [localError, setLocalError] = useState<string | null>(null)
  const [libraryEntries, setLibraryEntries] = useState<ProjectedAssetLibraryEntry[]>([])
  const [libraryQuery, setLibraryQuery] = useState('')
  const [libraryLoading, setLibraryLoading] = useState(false)
  const [libraryLoaded, setLibraryLoaded] = useState(false)
  const [libraryError, setLibraryError] = useState<string | null>(null)
  const assetLibraryService = useMemo(() => getDefaultAssetLibraryService(), [])

  useLayoutEffect(() => {
    if (ioRowRef.current) {
      const center = ioRowRef.current.offsetTop + ioRowRef.current.offsetHeight / 2
      setHandleTop(`${center}px`)
    }
  }, [])

  const filePath = data.params.filePath as string | undefined
  const preview = data.params.preview as string | undefined

  const browse = useCallback(async () => {
    setLocalError(null)
    setLocalLoading(true)
    try {
      const path = await window.polykit.fs.selectImage()
      if (!path) return
      const base64 = await window.polykit.fs.readFileBase64(path)
      const src = `data:${mimeFromPath(path)};base64,${base64}`
      // Persist the image on the server so it survives reloads and works for
      // remote Web clients. A local-only path is not a valid workflow input.
      const uploaded = await window.polykit.fs.uploadImage(path)
      if (!uploaded) throw new Error(t('workflows.imageUploadFailed'))
      updateNodeData(id, { params: { ...data.params, filePath: uploaded, preview: src } })
      setPickerOpen(false)
    } catch (error) {
      setLocalError(error instanceof Error ? error.message : t('workflows.imageUploadFailed'))
    } finally {
      setLocalLoading(false)
    }
  }, [data.params, id, t, updateNodeData])

  const loadLibrary = useCallback(async () => {
    setLibraryLoaded(true)
    setLibraryLoading(true)
    setLibraryError(null)
    try {
      const result = await assetLibraryService.list()
      if (!result.success) {
        setLibraryError(result.error.message)
        return
      }
      setLibraryEntries(result.entries.filter((entry) => (
        entry.capability === 'image' && entry.state === 'ready' && entry.openable
      )))
      setLibraryLoaded(true)
    } catch (error) {
      setLibraryError(error instanceof Error ? error.message : t('workflows.imageLibraryError'))
    } finally {
      setLibraryLoading(false)
    }
  }, [assetLibraryService, t])

  useEffect(() => {
    if (!pickerOpen || pickerTab !== 'library' || libraryLoaded || libraryLoading) return
    void loadLibrary()
  }, [libraryLoaded, libraryLoading, loadLibrary, pickerOpen, pickerTab])

  const filteredLibraryEntries = useMemo(() => {
    const query = libraryQuery.trim().toLocaleLowerCase()
    if (!query) return libraryEntries
    return libraryEntries.filter((entry) => (
      entry.displayName.toLocaleLowerCase().includes(query)
      || entry.workspacePath.toLocaleLowerCase().includes(query)
    ))
  }, [libraryEntries, libraryQuery])

  const selectLibraryImage = useCallback((entry: ProjectedAssetLibraryEntry) => {
    updateNodeData(id, {
      params: {
        ...data.params,
        filePath: entry.workspacePath,
        preview: imagePreviewUrl(entry, apiUrl),
      },
    })
    setPickerOpen(false)
  }, [apiUrl, data.params, id, updateNodeData])

  const openPicker = (open: boolean) => {
    setPickerOpen(open)
    if (open) {
      setPickerTab('local')
      setLocalError(null)
      setLibraryError(null)
    }
  }

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
      <Popover open={pickerOpen} onOpenChange={openPicker}>
        <PopoverTrigger asChild>
          {preview ? (
            <Button
              type="button"
              variant="ghost"
              className="nodrag group relative h-auto flex-1 overflow-hidden rounded-b-md rounded-t-none p-0"
              aria-label={t('workflows.imageChange')}
            >
              <img src={preview} alt={filePath?.split(/[\\/]/).pop() ?? ''} className="h-full w-full object-cover" />
              <span className="absolute inset-0 flex items-center justify-center bg-black/0 transition-colors group-hover:bg-black/40">
                <span className="text-[10px] font-medium text-white opacity-0 transition-opacity group-hover:opacity-100">{t('workflows.imageChange')}</span>
              </span>
            </Button>
          ) : (
            <Button
              type="button"
              variant="outline"
              size="sm"
              className="nodrag m-3 w-[calc(100%-1.5rem)] justify-start gap-2 border-dashed text-muted-foreground hover:text-foreground"
            >
              <Images className="h-3.5 w-3.5" />
              {t('workflows.imageChoose')}
            </Button>
          )}
        </PopoverTrigger>
        <PopoverContent side="right" align="start" className="w-72 overflow-hidden p-0">
          <div className="border-b border-divider bg-card/70 px-3 pt-3">
            <div className="flex items-center gap-1 rounded-md bg-muted/35 p-1" role="tablist" aria-label={t('workflows.imageSource')}>
              <Button
                type="button"
                variant={pickerTab === 'local' ? 'secondary' : 'ghost'}
                size="sm"
                role="tab"
                aria-selected={pickerTab === 'local'}
                className="h-7 flex-1 gap-1.5 px-2 text-[11px]"
                onClick={() => setPickerTab('local')}
              >
                <FolderOpen className="h-3 w-3" />
                {t('workflows.imageLocal')}
              </Button>
              <Button
                type="button"
                variant={pickerTab === 'library' ? 'secondary' : 'ghost'}
                size="sm"
                role="tab"
                aria-selected={pickerTab === 'library'}
                className="h-7 flex-1 gap-1.5 px-2 text-[11px]"
                onClick={() => setPickerTab('library')}
              >
                <Images className="h-3 w-3" />
                {t('workflows.imageLibrary')}
              </Button>
            </div>
          </div>

          {pickerTab === 'local' ? (
            <div className="space-y-2.5 p-3">
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={() => { void browse() }}
                disabled={localLoading}
                className="nodrag w-full justify-start gap-2 border-dashed"
              >
                {localLoading ? <LoaderCircle className="h-3.5 w-3.5 animate-spin" /> : <FolderOpen className="h-3.5 w-3.5" />}
                {localLoading ? t('workflows.imageUploading') : t('workflows.imageChooseLocal')}
              </Button>
              <p className="text-[10px] leading-relaxed text-muted-foreground">{t('workflows.imageLocalHint')}</p>
              {localError && <p role="alert" className="text-[10px] leading-relaxed text-amber-400">{localError}</p>}
            </div>
          ) : (
            <div className="space-y-2.5 p-3">
              <div className="relative">
                <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
                <Input
                  value={libraryQuery}
                  onChange={(event) => setLibraryQuery(event.target.value)}
                  placeholder={t('workflows.imageLibrarySearch')}
                  aria-label={t('workflows.imageLibrarySearch')}
                  className="nodrag h-8 pl-8 pr-2 text-[11px]"
                />
              </div>
              <div className="flex items-center gap-2">
                <span className="text-[10px] text-muted-foreground">{t('workflows.imageLibraryCount', { count: filteredLibraryEntries.length })}</span>
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  className="nodrag ml-auto h-7 w-7"
                  onClick={() => { setLibraryLoaded(false); void loadLibrary() }}
                  disabled={libraryLoading}
                  title={t('workflows.imageLibraryRefresh')}
                  aria-label={t('workflows.imageLibraryRefresh')}
                >
                  <RefreshCw className={`h-3.5 w-3.5 ${libraryLoading ? 'animate-spin' : ''}`} />
                </Button>
              </div>
              {libraryLoading ? (
                <div className="flex items-center justify-center gap-2 py-8 text-[10px] text-muted-foreground">
                  <LoaderCircle className="h-3.5 w-3.5 animate-spin" />
                  {t('workflows.imageLibraryLoading')}
                </div>
              ) : libraryError ? (
                <p role="alert" className="py-3 text-[10px] leading-relaxed text-amber-400">{libraryError}</p>
              ) : filteredLibraryEntries.length === 0 ? (
                <p className="py-6 text-center text-[10px] text-muted-foreground">
                  {libraryEntries.length === 0 ? t('workflows.imageLibraryEmpty') : t('workflows.imageLibraryNoMatch')}
                </p>
              ) : (
                <div className="nodrag grid max-h-56 grid-cols-3 gap-2 overflow-y-auto pr-0.5">
                  {filteredLibraryEntries.map((entry) => (
                    <button
                      key={entry.id}
                      type="button"
                      aria-pressed={entry.workspacePath === filePath}
                      className={`group min-w-0 overflow-hidden rounded-md border bg-muted/20 text-left transition-colors hover:border-primary/60 hover:bg-primary/5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring ${entry.workspacePath === filePath ? 'border-primary ring-1 ring-primary/40' : 'border-divider'}`}
                      onClick={() => selectLibraryImage(entry)}
                      title={entry.workspacePath}
                    >
                      <span className="alpha-checker relative flex aspect-square items-center justify-center overflow-hidden">
                        <img src={imagePreviewUrl(entry, apiUrl)} alt="" loading="lazy" className="h-full w-full object-contain transition-transform duration-200 group-hover:scale-[1.04]" />
                      </span>
                      <span className="block truncate px-1.5 py-1.5 text-[9px] text-muted-foreground group-hover:text-foreground">{entry.displayName}</span>
                    </button>
                  ))}
                </div>
              )}
            </div>
          )}
        </PopoverContent>
      </Popover>
    </BaseNode>
  )
}

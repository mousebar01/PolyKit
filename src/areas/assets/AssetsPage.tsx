import { lazy, Suspense, useState, useRef, useCallback, useEffect, useMemo } from 'react'
import type { KeyboardEvent as ReactKeyboardEvent, PointerEvent as ReactPointerEvent, ReactNode, WheelEvent as ReactWheelEvent } from 'react'
import {
  LoaderCircle,
  Maximize2,
  Move,
  Redo2,
  RotateCw,
  Scan,
  Sparkles,
  Sun,
  Triangle,
  Undo2,
  ZoomIn,
  ZoomOut,
} from 'lucide-react'

import {
  Button,
  ColorPicker,
  Input,
  Label,
  Popover,
  PopoverContent,
  PopoverTrigger,
  Slider,
} from '@shared/components/ui'
import { useApi } from '@shared/hooks/useApi'
import { useI18n } from '@shared/i18n'
import { useAppStore, DEFAULT_LIGHT_SETTINGS } from '@shared/stores/appStore'
import type { GenerationJob, LightSettings } from '@shared/stores/appStore'
import GenerationHUD from './components/GenerationHUD'
import AssetLibrarySidebar, { type AssetExportFormat } from './components/AssetLibrarySidebar'
import { getDefaultAssetLibraryService } from './assetLibraryService'
import { AssetDeleteDialog, AssetRenameDialog } from './components/AssetManageDialogs'
import { resolveAssetLibraryOpenTarget, type ProjectedAssetLibraryEntry } from './assetLibraryProjection'
import {
  buildAssetLibraryOpenRequest,
  createAssetLibraryOpenJob,
  describeAssetLibraryOpenability,
  getDefaultAssetLibraryCollapsedSectionKeys,
  isAssetLibraryEntryOpenable,
  toggleAssetLibrarySectionKey,
  type AssetsOpenPanel,
  type AssetLibrarySortMode,
} from './assetLibraryUi'

const Viewer3D = lazy(() => import('./components/Viewer3D'))

function AssetsLoading({ label }: { label: string }): JSX.Element {
  return (
    <div className="flex flex-1 items-center justify-center bg-card" role="status" aria-live="polite">
      <div className="flex items-center gap-2 text-xs text-muted-foreground">
        <LoaderCircle className="h-4 w-4 animate-spin" />
        {label}
      </div>
    </div>
  )
}

function resolveAssetUrl(apiUrl: string, outputUrl: string): string {
  if (/^(?:https?:|blob:|data:)/i.test(outputUrl)) return outputUrl
  return `${apiUrl}${outputUrl}`
}

const MIN_IMAGE_ZOOM = 0.25
const MAX_IMAGE_ZOOM = 8
const IMAGE_ZOOM_FACTOR = 1.2

interface ImagePoint {
  x: number
  y: number
}

function clampImageZoom(value: number): number {
  return Math.min(MAX_IMAGE_ZOOM, Math.max(MIN_IMAGE_ZOOM, Number(value.toFixed(2))))
}

function AssetImageViewer({ apiUrl, outputUrl, alt }: { apiUrl: string, outputUrl: string, alt: string }): JSX.Element {
  const { t } = useI18n()
  const viewportRef = useRef<HTMLDivElement>(null)
  const dragRef = useRef<{ pointerId: number, start: ImagePoint, offset: ImagePoint } | null>(null)
  const zoomRef = useRef(1)
  const offsetRef = useRef<ImagePoint>({ x: 0, y: 0 })
  const [zoom, setZoom] = useState(1)
  const [offset, setOffset] = useState<ImagePoint>({ x: 0, y: 0 })
  const [dragging, setDragging] = useState(false)

  useEffect(() => {
    zoomRef.current = 1
    offsetRef.current = { x: 0, y: 0 }
    setZoom(1)
    setOffset({ x: 0, y: 0 })
    dragRef.current = null
    setDragging(false)
  }, [outputUrl])

  const fitImage = useCallback(() => {
    zoomRef.current = 1
    offsetRef.current = { x: 0, y: 0 }
    setZoom(1)
    setOffset({ x: 0, y: 0 })
  }, [])

  const zoomTo = useCallback((requestedZoom: number, anchor: ImagePoint = { x: 0, y: 0 }) => {
    const currentZoom = zoomRef.current
    const nextZoom = clampImageZoom(requestedZoom)
    if (nextZoom === currentZoom) return

    const currentOffset = offsetRef.current
    const nextOffset = {
      x: anchor.x - (anchor.x - currentOffset.x) * (nextZoom / currentZoom),
      y: anchor.y - (anchor.y - currentOffset.y) * (nextZoom / currentZoom),
    }
    zoomRef.current = nextZoom
    offsetRef.current = nextOffset
    setZoom(nextZoom)
    setOffset(nextOffset)
  }, [])

  const zoomBy = useCallback((factor: number) => {
    zoomTo(zoomRef.current * factor)
  }, [zoomTo])

  const handleWheel = useCallback((event: ReactWheelEvent<HTMLDivElement>) => {
    event.preventDefault()
    const rect = viewportRef.current?.getBoundingClientRect()
    if (!rect) return
    const anchor = {
      x: event.clientX - rect.left - rect.width / 2,
      y: event.clientY - rect.top - rect.height / 2,
    }
    zoomTo(zoomRef.current * (event.deltaY < 0 ? IMAGE_ZOOM_FACTOR : 1 / IMAGE_ZOOM_FACTOR), anchor)
  }, [zoomTo])

  const handlePointerDown = useCallback((event: ReactPointerEvent<HTMLDivElement>) => {
    if (event.button !== 0 || zoomRef.current <= 1) return
    dragRef.current = {
      pointerId: event.pointerId,
      start: { x: event.clientX, y: event.clientY },
      offset: offsetRef.current,
    }
    event.currentTarget.setPointerCapture(event.pointerId)
    setDragging(true)
  }, [])

  const handlePointerMove = useCallback((event: ReactPointerEvent<HTMLDivElement>) => {
    const drag = dragRef.current
    if (!drag || drag.pointerId !== event.pointerId) return
    const nextOffset = {
      x: drag.offset.x + event.clientX - drag.start.x,
      y: drag.offset.y + event.clientY - drag.start.y,
    }
    offsetRef.current = nextOffset
    setOffset(nextOffset)
  }, [])

  const stopDragging = useCallback((event: ReactPointerEvent<HTMLDivElement>) => {
    if (dragRef.current?.pointerId !== event.pointerId) return
    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId)
    dragRef.current = null
    setDragging(false)
  }, [])

  const handleDoubleClick = useCallback(() => {
    if (zoomRef.current === 1) zoomBy(2)
    else fitImage()
  }, [fitImage, zoomBy])

  const handleKeyDown = useCallback((event: ReactKeyboardEvent<HTMLDivElement>) => {
    if (event.key === '+' || event.key === '=') {
      event.preventDefault()
      zoomBy(IMAGE_ZOOM_FACTOR)
    } else if (event.key === '-') {
      event.preventDefault()
      zoomBy(1 / IMAGE_ZOOM_FACTOR)
    } else if (event.key === '0') {
      event.preventDefault()
      fitImage()
    }
  }, [fitImage, zoomBy])

  return (
    <div
      ref={viewportRef}
      className={`alpha-checker relative flex h-full w-full items-center justify-center overflow-hidden p-8 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring ${dragging ? 'cursor-grabbing' : zoom > 1 ? 'cursor-grab' : 'cursor-default'}`}
      role="region"
      aria-label={alt}
      tabIndex={0}
      onWheel={handleWheel}
      onPointerDown={handlePointerDown}
      onPointerMove={handlePointerMove}
      onPointerUp={stopDragging}
      onPointerCancel={stopDragging}
      onDoubleClick={handleDoubleClick}
      onKeyDown={handleKeyDown}
    >
      <img
        src={resolveAssetUrl(apiUrl, outputUrl)}
        alt={alt}
        draggable={false}
        className={`block max-h-full max-w-full select-none object-contain ${dragging ? '' : 'transition-transform duration-150'}`}
        style={{
          transform: `translate3d(${offset.x}px, ${offset.y}px, 0) scale(${zoom})`,
          transformOrigin: 'center center',
        }}
      />
      <div className="pointer-events-none absolute inset-x-0 bottom-4 flex justify-center">
        <div
          className="pointer-events-auto flex items-center gap-1 rounded-lg border border-divider bg-card/95 p-1 backdrop-blur-sm"
          onPointerDown={(event) => event.stopPropagation()}
          onDoubleClick={(event) => event.stopPropagation()}
        >
          <Button
            type="button"
            variant="ghost"
            size="icon"
            className="h-8 w-8"
            onClick={() => zoomBy(1 / IMAGE_ZOOM_FACTOR)}
            disabled={zoom <= MIN_IMAGE_ZOOM}
            title={t('assets.zoomOut')}
            aria-label={t('assets.zoomOut')}
          >
            <ZoomOut className="h-4 w-4" />
          </Button>
          <span className="min-w-12 px-1 text-center text-[11px] tabular-nums text-muted-foreground" aria-live="polite">
            {Math.round(zoom * 100)}%
          </span>
          <Button
            type="button"
            variant="ghost"
            size="icon"
            className="h-8 w-8"
            onClick={() => zoomBy(IMAGE_ZOOM_FACTOR)}
            disabled={zoom >= MAX_IMAGE_ZOOM}
            title={t('assets.zoomIn')}
            aria-label={t('assets.zoomIn')}
          >
            <ZoomIn className="h-4 w-4" />
          </Button>
          <div className="mx-1 h-5 w-px bg-divider" aria-hidden="true" />
          <Button
            type="button"
            variant="ghost"
            size="icon"
            className="h-8 w-8"
            onClick={fitImage}
            disabled={zoom === 1 && offset.x === 0 && offset.y === 0}
            title={t('assets.fitImage')}
            aria-label={t('assets.fitImage')}
          >
            <Scan className="h-4 w-4" />
          </Button>
        </div>
      </div>
    </div>
  )
}

const MIN_WIDTH = 220
const MAX_WIDTH = 440
const DEFAULT_WIDTH = 280

function ToolButton({
  label,
  active,
  onClick,
  children,
}: {
  label: string
  active: boolean
  onClick: () => void
  children: ReactNode
}): JSX.Element {
  return (
    <Button
      type="button"
      variant={active ? 'secondary' : 'outline'}
      size="icon"
      className="h-8 w-8"
      onClick={onClick}
      title={label}
      aria-label={label}
      aria-pressed={active}
    >
      {children}
    </Button>
  )
}

function DecimatePopover({
  currentTriangles,
  decimating,
  onDecimate,
  onClose,
}: {
  currentTriangles: number | null
  decimating: boolean
  onDecimate: (targetFaces: number) => void
  onClose: () => void
}): JSX.Element {
  const defaultTarget = currentTriangles ? Math.round(currentTriangles * 0.5) : 5000
  const [inputValue, setInputValue] = useState(String(defaultTarget))
  const parsed = parseInt(inputValue, 10)
  const validTarget = !isNaN(parsed) && parsed >= 100 ? parsed : null
  const reduction = currentTriangles && validTarget
    ? Math.round((1 - Math.min(validTarget, currentTriangles) / currentTriangles) * 100)
    : null
  const { t } = useI18n()

  return (
    <PopoverContent align="start" className="w-[240px] p-4">
      <div className="flex flex-col gap-3.5">
        <p className="text-xs font-medium text-foreground">{t('assets.decimateTitle')}</p>
        {currentTriangles && (
          <p className="text-[11px] text-muted-foreground">
            {t('assets.decimateCurrent')}
            <span className="tabular-nums text-foreground">{currentTriangles.toLocaleString()} tri</span>
          </p>
        )}
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="asset-decimate-target" className="text-[11px] text-muted-foreground">
            {t('assets.decimateTarget')}
          </Label>
          <Input
            id="asset-decimate-target"
            type="number"
            value={inputValue}
            onChange={(event) => setInputValue(event.target.value)}
            min={100}
            step={500}
            className="h-8 text-xs"
          />
          {reduction !== null && (
            <p className="text-[11px] tabular-nums text-muted-foreground">
              {t('assets.decimateReduction')}
              <span className="font-medium text-primary">{reduction}%</span>
            </p>
          )}
        </div>
        <div className="flex gap-2">
          <Button type="button" variant="outline" size="sm" className="flex-1" onClick={onClose}>
            Cancel
          </Button>
          <Button
            type="button"
            size="sm"
            className="flex-1 gap-1.5"
            onClick={() => validTarget && onDecimate(validTarget)}
            disabled={decimating || !validTarget}
          >
            {decimating && <LoaderCircle className="h-3.5 w-3.5 animate-spin" />}
            {decimating ? t('assets.processing') : t('assets.apply')}
          </Button>
        </div>
      </div>
    </PopoverContent>
  )
}

function LightPopover({
  settings,
  onChange,
  onClose,
}: {
  settings: LightSettings
  onChange: (settings: LightSettings) => void
  onClose: () => void
}): JSX.Element {
  const { t } = useI18n()

  function lightRow(
    label: string,
    colorKey: keyof LightSettings,
    intensityKey: keyof LightSettings,
    max: number,
  ): JSX.Element {
    const intensity = settings[intensityKey] as number
    const color = settings[colorKey] as string
    return (
      <div className="flex flex-col gap-1.5">
        <div className="flex items-center gap-2">
          <ColorPicker value={color} onChange={(nextColor) => onChange({ ...settings, [colorKey]: nextColor })} />
          <span className="flex-1 text-[11px] text-muted-foreground">{label}</span>
          <span className="font-mono text-[11px] tabular-nums text-muted-foreground">{intensity.toFixed(1)}</span>
        </div>
        <Slider
          min={0}
          max={max}
          step={0.1}
          value={[intensity]}
          onValueChange={([value]) => onChange({ ...settings, [intensityKey]: value })}
          aria-label={`${label} intensity`}
        />
      </div>
    )
  }

  function plainRow(label: string, intensityKey: keyof LightSettings, max: number): JSX.Element {
    const value = (settings[intensityKey] as number) ?? (DEFAULT_LIGHT_SETTINGS[intensityKey] as number)
    return (
      <div className="flex flex-col gap-1.5">
        <div className="flex items-center gap-2">
          <span className="flex-1 text-[11px] text-muted-foreground">{label}</span>
          <span className="font-mono text-[11px] tabular-nums text-muted-foreground">{value.toFixed(2)}</span>
        </div>
        <Slider
          min={0}
          max={max}
          step={0.05}
          value={[value]}
          onValueChange={([nextValue]) => onChange({ ...settings, [intensityKey]: nextValue })}
          aria-label={`${label} intensity`}
        />
      </div>
    )
  }

  return (
    <PopoverContent align="end" className="w-[260px] p-4">
      <div className="flex flex-col gap-3.5">
        <div className="flex items-center justify-between gap-3">
          <p className="text-xs font-medium text-foreground">{t('assets.lighting')}</p>
          <Button type="button" variant="ghost" size="sm" className="h-7 px-2" onClick={() => onChange(DEFAULT_LIGHT_SETTINGS)}>
            {t('assets.reset')}
          </Button>
        </div>
        {lightRow(t('assets.lightSun'), 'mainColor', 'mainIntensity', 4)}
        {lightRow(t('assets.lightFill'), 'fillColor', 'fillIntensity', 2)}
        {plainRow(t('assets.lightAmbient'), 'ambientIntensity', 1.5)}
        {plainRow(t('assets.lightEnvironment'), 'envIntensity', 2)}
        <Button type="button" variant="outline" size="sm" onClick={onClose}>
          {t('assets.close')}
        </Button>
      </div>
    </PopoverContent>
  )
}

function SmoothPopover({
  smoothing,
  onSmooth,
  onClose,
}: {
  smoothing: boolean
  onSmooth: (iterations: number) => void
  onClose: () => void
}): JSX.Element {
  const [inputValue, setInputValue] = useState('3')
  const parsed = parseInt(inputValue, 10)
  const valid = !isNaN(parsed) && parsed >= 1 && parsed <= 20
  const { t } = useI18n()

  return (
    <PopoverContent align="start" className="w-[220px] p-4">
      <div className="flex flex-col gap-3.5">
        <p className="text-xs font-medium text-foreground">{t('assets.smooth')}</p>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="asset-smooth-iterations" className="text-[11px] text-muted-foreground">
            {t('assets.iterations')} <span className="tabular-nums">(1–20)</span>
          </Label>
          <Input
            id="asset-smooth-iterations"
            type="number"
            value={inputValue}
            onChange={(event) => setInputValue(event.target.value)}
            min={1}
            max={20}
            step={1}
            className="h-8 text-xs"
          />
          <p className="text-[11px] text-muted-foreground">{t('assets.smoothHint')}</p>
        </div>
        <div className="flex gap-2">
          <Button type="button" variant="outline" size="sm" className="flex-1" onClick={onClose}>
            Cancel
          </Button>
          <Button
            type="button"
            size="sm"
            className="flex-1 gap-1.5"
            onClick={() => valid && onSmooth(parsed)}
            disabled={smoothing || !valid}
          >
            {smoothing && <LoaderCircle className="h-3.5 w-3.5 animate-spin" />}
            {smoothing ? t('common.processing') : t('assets.apply')}
          </Button>
        </div>
      </div>
    </PopoverContent>
  )
}

export default function AssetsPage(): JSX.Element {
  const { t } = useI18n()
  const [panelWidth, setPanelWidth] = useState(DEFAULT_WIDTH)
  const [openPanel, setOpenPanel] = useState<AssetsOpenPanel>(null)
  const [decimating, setDecimating] = useState(false)
  const [smoothing, setSmoothing] = useState(false)
  const [importing, setImporting] = useState(false)
  const [libraryEntries, setLibraryEntries] = useState<ProjectedAssetLibraryEntry[]>([])
  const [librarySelectedEntryId, setLibrarySelectedEntryId] = useState<string | null>(null)
  const [libraryLoaded, setLibraryLoaded] = useState(false)
  const [libraryLoading, setLibraryLoading] = useState(false)
  const [libraryOpening, setLibraryOpening] = useState(false)
  const [libraryError, setLibraryError] = useState<string | null>(null)
  const [librarySearchQuery, setLibrarySearchQuery] = useState('')
  const [libraryRenameTarget, setLibraryRenameTarget] = useState<ProjectedAssetLibraryEntry | null>(null)
  const [libraryDeleteTargets, setLibraryDeleteTargets] = useState<ProjectedAssetLibraryEntry[] | null>(null)
  const [librarySortMode, setLibrarySortMode] = useState<AssetLibrarySortMode>('date')
  const [libraryCollapsedSectionKeys, setLibraryCollapsedSectionKeys] = useState<string[]>(() => getDefaultAssetLibraryCollapsedSectionKeys())
  const [gizmoMode, setGizmoMode] = useState<'translate' | 'rotate' | 'scale' | null>(null)
  const dragging = useRef(false)
  const gizmoUndoRef = useRef<(() => boolean) | null>(null)
  const libraryRefreshKeyRef = useRef<string | null>(null)

  const lightSettings = useAppStore((state) => state.lightSettings)
  const setLightSettings = useAppStore((state) => state.setLightSettings)
  const currentJob = useAppStore((state) => state.currentJob)
  const apiUrl = useAppStore((state) => state.apiUrl)
  const showError = useAppStore((state) => state.showError)
  const updateCurrentJob = useAppStore((state) => state.updateCurrentJob)
  const setCurrentJob = useAppStore((state) => state.setCurrentJob)
  const meshStats = useAppStore((state) => state.meshStats)
  const meshSelected = useAppStore((state) => state.meshSelected)
  const pushMeshUrl = useAppStore((state) => state.pushMeshUrl)
  const undoMesh = useAppStore((state) => state.undoMesh)
  const redoMesh = useAppStore((state) => state.redoMesh)
  const canUndo = useAppStore((state) => state.historyIndex > 0)
  const canRedo = useAppStore((state) => state.historyIndex < state.meshHistory.length - 1)
  const { optimizeMesh, smoothMesh, importMesh } = useApi()
  const assetLibraryService = useMemo(() => getDefaultAssetLibraryService(), [])

  useEffect(() => {
    const handler = (event: KeyboardEvent) => {
      if (!event.ctrlKey && !event.metaKey) return
      if (event.key === 'z') {
        event.preventDefault()
        if (gizmoUndoRef.current?.()) return
        undoMesh()
      }
      if (event.key === 'y') {
        event.preventDefault()
        redoMesh()
      }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [undoMesh, redoMesh])

  const hasOutput = currentJob?.status === 'done' && !!currentJob.outputUrl
  const hasImage = hasOutput && (currentJob?.outputKind === 'image' || /\.(png|jpe?g|webp|gif|bmp)(?:[?#]|$)/i.test(currentJob.outputUrl ?? ''))
  const hasModel = hasOutput && !hasImage

  useEffect(() => {
    if (!meshSelected) setGizmoMode(null)
  }, [meshSelected])

  useEffect(() => {
    const handler = (event: KeyboardEvent) => {
      const element = document.activeElement as HTMLElement | null
      if (element && (element instanceof HTMLInputElement || element instanceof HTMLTextAreaElement || element.isContentEditable)) return
      if (event.key === 'Escape') {
        setGizmoMode((mode) => (mode ? null : mode))
        return
      }
      if (!hasModel || !meshSelected) return
      const key = event.key.toLowerCase()
      if (key === 'w') setGizmoMode('translate')
      else if (key === 'r') setGizmoMode('rotate')
      else if (key === 's') setGizmoMode('scale')
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [hasModel, meshSelected])

  useEffect(() => {
    if (libraryLoaded || libraryLoading) return
    void loadLibraryEntries()
    // eslint-disable-next-line react-hooks/exhaustive-deps -- load once on mount
  }, [libraryLoaded, libraryLoading])

  useEffect(() => {
    if (!libraryLoaded || libraryLoading || currentJob?.status !== 'done' || !currentJob.outputUrl) return
    const refreshKey = `${currentJob.id}:${currentJob.outputUrl}`
    if (libraryRefreshKeyRef.current === refreshKey) return
    libraryRefreshKeyRef.current = refreshKey
    // The server prewarms the thumbnail when the output is published; refresh
    // the list once so the newly generated card appears without a manual reload.
    void loadLibraryEntries()
    // eslint-disable-next-line react-hooks/exhaustive-deps -- refresh only when a new output is complete
  }, [currentJob?.id, currentJob?.outputUrl, currentJob?.status, libraryLoaded, libraryLoading])

  function handleExportAssets(workspacePaths: string[], format: AssetExportFormat) {
    const safePaths = [...new Set(workspacePaths.map((workspacePath) => workspacePath.replace(/\\/g, '/').trim()))]
      .filter((workspacePath) => {
        return /^Workflows\//.test(workspacePath)
          && !workspacePath.split('/').includes('..')
          && !/%2e|%2f|%5c/i.test(workspacePath)
      })
    if (format === 'original') {
      if (safePaths.length > 1) {
        void fetch(`${apiUrl}/workspace-library/export`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ workspacePaths: safePaths }),
        }).then(async (response) => {
          if (!response.ok) throw new Error(`Could not export selected assets (HTTP ${response.status}).`)
          const blobUrl = URL.createObjectURL(await response.blob())
          const link = document.createElement('a')
          link.href = blobUrl
          link.download = 'polykit-assets.zip'
          link.rel = 'noopener'
          link.click()
          link.remove()
          window.setTimeout(() => URL.revokeObjectURL(blobUrl), 0)
        }).catch((error) => {
          showError(error instanceof Error ? error.message : String(error))
        })
        return
      }
      void Promise.all(safePaths.map(async (workspacePath) => {
        const urlPath = workspacePath.split('/').map((segment) => encodeURIComponent(segment)).join('/')
        const response = await fetch(`${apiUrl}/workspace/${urlPath}`)
        if (!response.ok) throw new Error(`Could not download ${workspacePath} (HTTP ${response.status}).`)
        const blobUrl = URL.createObjectURL(await response.blob())
        const link = document.createElement('a')
        link.href = blobUrl
        link.download = workspacePath.split('/').pop() ?? 'asset'
        link.rel = 'noopener'
        link.click()
        link.remove()
        window.setTimeout(() => URL.revokeObjectURL(blobUrl), 0)
      })).catch((error) => {
        showError(error instanceof Error ? error.message : String(error))
      })
      return
    }
    safePaths.forEach((workspacePath) => {
      const sourceName = workspacePath.split('/').pop() ?? 'asset'
      const stem = sourceName.replace(/\.[^.]+$/, '') || 'asset'
      const link = document.createElement('a')
      link.href = `${apiUrl}/export/${format}?path=${encodeURIComponent(workspacePath)}`
      link.download = `${stem}.${format}`
      link.rel = 'noopener'
      link.click()
      link.remove()
    })
  }

  function getOptimizePath(url: string): string {
    if (url.startsWith('/workspace/')) return url.slice('/workspace/'.length)
    if (url.startsWith('/optimize/serve-file?path=')) return decodeURIComponent(url.split('path=')[1] ?? '')
    return url
  }

  async function handleImportMesh() {
    const filePath = await window.polykit.fs.selectMeshFile()
    if (!filePath) return
    setOpenPanel(null)
    setImporting(true)
    try {
      const { url } = await importMesh(filePath)
      const job: GenerationJob = {
        id: `import-${Date.now()}`,
        imageFile: '',
        status: 'done',
        progress: 100,
        outputUrl: url,
        originalOutputUrl: url,
        createdAt: Date.now(),
      }
      setCurrentJob(job)
      pushMeshUrl(url)
    } finally {
      setImporting(false)
    }
  }

  async function loadLibraryEntries() {
    setLibraryLoading(true)
    setLibraryError(null)
    try {
      const result = await assetLibraryService.list()
      if (!result.success) {
        setLibraryLoaded(false)
        setLibraryEntries([])
        setLibrarySelectedEntryId(null)
        setLibraryError(result.error.message)
        return
      }
      setLibraryEntries(result.entries)
      setLibrarySelectedEntryId((current) => current && result.entries.some((entry) => entry.id === current)
        ? current
        : result.entries.find(isAssetLibraryEntryOpenable)?.id ?? result.entries[0]?.id ?? null)
      setLibraryLoaded(true)
    } catch (error) {
      setLibraryLoaded(false)
      setLibraryEntries([])
      setLibrarySelectedEntryId(null)
      setLibraryError(error instanceof Error ? error.message : String(error))
    } finally {
      setLibraryLoading(false)
    }
  }

  async function confirmRenameAsset(newName: string) {
    const entry = libraryRenameTarget
    if (!entry) return
    setLibraryError(null)
    try {
      const result = await assetLibraryService.rename({ workspacePath: entry.workspacePath, newName })
      if (!result.success) {
        setLibraryError(`Rename failed — ${entry.workspacePath} is not a valid server asset.`)
        setLibraryRenameTarget(null)
        return
      }
      setLibraryRenameTarget(null)
      await loadLibraryEntries()
    } catch (error) {
      setLibraryError(error instanceof Error ? error.message : String(error))
      setLibraryRenameTarget(null)
    }
  }

  async function confirmDeleteAssets() {
    const targets = libraryDeleteTargets
    if (!targets || targets.length === 0) return
    setLibraryError(null)
    try {
      await assetLibraryService.delete({ workspacePaths: targets.map((entry) => entry.workspacePath) })
      setLibraryDeleteTargets(null)
      await loadLibraryEntries()
    } catch (error) {
      setLibraryError(error instanceof Error ? error.message : String(error))
      setLibraryDeleteTargets(null)
    }
  }

  async function handleOpenSelectedLibraryEntry() {
    const selectedEntry = libraryEntries.find((entry) => entry.id === librarySelectedEntryId) ?? null
    if (!selectedEntry) {
      setLibraryError('Select an asset to open it in the viewer.')
      return
    }
    if (!isAssetLibraryEntryOpenable(selectedEntry)) {
      setLibraryError(describeAssetLibraryOpenability(selectedEntry))
      return
    }

    setLibraryOpening(true)
    setLibraryError(null)
    try {
      const result = await assetLibraryService.open(buildAssetLibraryOpenRequest(selectedEntry))
      if (!result.success) {
        setLibraryError(result.error.message)
        return
      }
      const target = resolveAssetLibraryOpenTarget(result.entry)
      const selection = createAssetLibraryOpenJob(target)
      if (!selection) {
        setLibraryError(describeAssetLibraryOpenability(result.entry))
        return
      }
      setLibraryEntries((currentEntries) => currentEntries.map((entry) => entry.id === result.entry.id ? result.entry : entry))
      setLibrarySelectedEntryId(result.entry.id)
      setCurrentJob(selection.job)
      if ((target.kind === 'self' && target.assetKind === 'mesh') || target.kind === 'linked-source') {
        pushMeshUrl(selection.historyUrl)
      }
    } catch (error) {
      setLibraryError(error instanceof Error ? error.message : String(error))
    } finally {
      setLibraryOpening(false)
    }
  }

  async function handleSmooth(iterations: number) {
    if (!currentJob?.outputUrl) return
    setSmoothing(true)
    try {
      const path = getOptimizePath(currentJob.outputUrl)
      const { url } = await smoothMesh(path, iterations)
      updateCurrentJob({ outputUrl: url })
      pushMeshUrl(url)
      setOpenPanel(null)
    } catch (error) {
      showError(error instanceof Error ? error.message : String(error))
    } finally {
      setSmoothing(false)
    }
  }

  async function handleDecimate(targetFaces: number) {
    if (!currentJob?.outputUrl) return
    setDecimating(true)
    try {
      const path = getOptimizePath(currentJob.outputUrl)
      const { url } = await optimizeMesh(path, targetFaces)
      updateCurrentJob({ outputUrl: url })
      pushMeshUrl(url)
      setOpenPanel(null)
    } catch (error) {
      showError(error instanceof Error ? error.message : String(error))
    } finally {
      setDecimating(false)
    }
  }

  const onMouseDown = useCallback((event: React.MouseEvent) => {
    event.preventDefault()
    dragging.current = true

    const onMouseMove = (moveEvent: MouseEvent) => {
      if (!dragging.current) return
      setPanelWidth((width) => Math.min(MAX_WIDTH, Math.max(MIN_WIDTH, width + moveEvent.movementX)))
    }
    const onMouseUp = () => {
      dragging.current = false
      window.removeEventListener('mousemove', onMouseMove)
      window.removeEventListener('mouseup', onMouseUp)
    }
    window.addEventListener('mousemove', onMouseMove)
    window.addEventListener('mouseup', onMouseUp)
  }, [])

  return (
    <>
      <div className="flex shrink-0 flex-col overflow-hidden" style={{ width: panelWidth }}>
        <AssetLibrarySidebar
          thumbnailBase={apiUrl}
          entries={libraryEntries}
          selectedEntryId={librarySelectedEntryId}
          loading={libraryLoading}
          opening={libraryOpening}
           importing={importing}
          error={libraryError}
          searchQuery={librarySearchQuery}
          sortMode={librarySortMode}
          collapsedSectionKeys={libraryCollapsedSectionKeys}
          onSelectEntry={(entryId) => {
            setLibraryError(null)
            setLibrarySelectedEntryId(entryId)
          }}
          onSearchQueryChange={setLibrarySearchQuery}
          onSortModeChange={setLibrarySortMode}
          onToggleSection={(sectionKey) => setLibraryCollapsedSectionKeys((current) => toggleAssetLibrarySectionKey(current, sectionKey))}
          onOpenSelected={() => { void handleOpenSelectedLibraryEntry() }}
           onImport={() => { void handleImportMesh() }}
          onExport={handleExportAssets}
          onRefresh={() => { void loadLibraryEntries() }}
          onRename={(entry) => setLibraryRenameTarget(entry)}
          onDelete={(workspacePaths) => {
            const targets = libraryEntries.filter((entry) => workspacePaths.includes(entry.workspacePath))
            setLibraryDeleteTargets(targets.length > 0 ? targets : workspacePaths.map((path) => ({
              id: path,
              workspacePath: path,
              displayName: path.split('/').pop() ?? path,
              state: 'ready' as const,
              previewKind: 'binary' as const,
              warnings: [],
              openable: false,
            })))
          }}
        />
      </div>

      <div
        onMouseDown={onMouseDown}
        className="w-2 shrink-0 cursor-col-resize bg-transparent transition-colors hover:bg-primary/30 active:bg-primary/50"
      />

      {/* Keep the viewport as one clipped surface so its toolbar, canvas, and
          overlays share the same rounded outer corners. */}
      <div className="flex flex-1 flex-col overflow-hidden rounded-lg bg-background">
        <div className="flex h-10 shrink-0 items-center gap-2 overflow-x-auto overflow-y-hidden border-b border-divider bg-card/65 px-2.5 py-1">
          <Button type="button" variant="outline" size="icon" className="shrink-0" onClick={undoMesh} disabled={!canUndo} title="Undo (Ctrl+Z)" aria-label="Undo">
            <Undo2 className="h-4 w-4" />
          </Button>
          <Button type="button" variant="outline" size="icon" className="shrink-0" onClick={redoMesh} disabled={!canRedo} title="Redo (Ctrl+Y)" aria-label="Redo">
            <Redo2 className="h-4 w-4" />
          </Button>

          {hasModel && (
            <>
              <Popover open={openPanel === 'smooth'} onOpenChange={(open) => setOpenPanel(open ? 'smooth' : null)}>
                <PopoverTrigger asChild>
                  <Button
                    type="button"
                    variant={openPanel === 'smooth' || smoothing ? 'secondary' : 'outline'}
                    size="sm"
                    className="shrink-0 gap-1.5"
                    disabled={smoothing}
                  >
                    {smoothing ? <LoaderCircle className="h-4 w-4 animate-spin" /> : <Sparkles className="h-4 w-4" />}
                    {smoothing ? t('common.processing') : t('assets.smooth')}
                  </Button>
                </PopoverTrigger>
                <SmoothPopover smoothing={smoothing} onSmooth={handleSmooth} onClose={() => setOpenPanel(null)} />
              </Popover>

              <Popover open={openPanel === 'decimate'} onOpenChange={(open) => setOpenPanel(open ? 'decimate' : null)}>
                <PopoverTrigger asChild>
                  <Button
                    type="button"
                    variant={openPanel === 'decimate' || decimating ? 'secondary' : 'outline'}
                    size="sm"
                    className="shrink-0 gap-1.5"
                    disabled={decimating}
                  >
                    {decimating ? <LoaderCircle className="h-4 w-4 animate-spin" /> : <Triangle className="h-4 w-4" />}
                    {decimating ? t('common.processing') : t('assets.decimate')}
                  </Button>
                </PopoverTrigger>
                <DecimatePopover
                  currentTriangles={meshStats?.triangles ?? null}
                  decimating={decimating}
                  onDecimate={handleDecimate}
                  onClose={() => setOpenPanel(null)}
                />
              </Popover>
            </>
          )}

          {hasModel && meshSelected && (
            <>
              <ToolButton label="Move" active={gizmoMode === 'translate'} onClick={() => setGizmoMode((mode) => (mode === 'translate' ? null : 'translate'))}>
                <Move className="h-4 w-4" />
              </ToolButton>
              <ToolButton label="Rotate" active={gizmoMode === 'rotate'} onClick={() => setGizmoMode((mode) => (mode === 'rotate' ? null : 'rotate'))}>
                <RotateCw className="h-4 w-4" />
              </ToolButton>
              <ToolButton label="Scale" active={gizmoMode === 'scale'} onClick={() => setGizmoMode((mode) => (mode === 'scale' ? null : 'scale'))}>
                <Maximize2 className="h-4 w-4" />
              </ToolButton>
            </>
          )}

          <div className="ml-auto shrink-0">
            <Popover open={openPanel === 'light'} onOpenChange={(open) => setOpenPanel(open ? 'light' : null)}>
              <PopoverTrigger asChild>
                <Button
                  type="button"
                  variant={openPanel === 'light' ? 'secondary' : 'outline'}
                  size="icon"
                  title={t('assets.lighting')}
                  aria-label={t('assets.lighting')}
                >
                  <Sun className="h-4 w-4" />
                </Button>
              </PopoverTrigger>
              <LightPopover settings={lightSettings} onChange={setLightSettings} onClose={() => setOpenPanel(null)} />
            </Popover>
          </div>
        </div>

        {/* Keep the viewer surface stable while the lazy Three.js canvas mounts. */}
        <div className="relative flex-1 overflow-hidden bg-card">
          {hasImage && currentJob?.outputUrl ? (
            <AssetImageViewer apiUrl={apiUrl} outputUrl={currentJob.outputUrl} alt={currentJob.imageFile || 'Generated image'} />
          ) : (
            <Suspense fallback={<AssetsLoading label="Loading 3D viewer…" />}>
              <Viewer3D lightSettings={lightSettings} gizmoMode={gizmoMode} gizmoUndoRef={gizmoUndoRef} />
            </Suspense>
          )}
          <GenerationHUD />
        </div>
      </div>

      {libraryRenameTarget && (
        <AssetRenameDialog
          currentName={libraryRenameTarget.displayName}
          onConfirm={(newName) => { void confirmRenameAsset(newName) }}
          onCancel={() => setLibraryRenameTarget(null)}
        />
      )}
      {libraryDeleteTargets && (
        <AssetDeleteDialog
          count={libraryDeleteTargets.length}
          displayName={libraryDeleteTargets.length === 1 ? libraryDeleteTargets[0].displayName : undefined}
          onConfirm={() => { void confirmDeleteAssets() }}
          onCancel={() => setLibraryDeleteTargets(null)}
        />
      )}
    </>
  )
}

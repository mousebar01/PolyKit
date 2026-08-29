import { useEffect, useRef, useState, type KeyboardEvent } from 'react'
import {
  ArrowDownUp,
  Box,
  CheckSquare,
  ChevronRight,
  ChevronDown,
  Download,
  Image as ImageIcon,
  LayoutGrid,
  List,
  Pencil,
  RefreshCw,
  Search,
  Square,
  Star,
  Trash2,
  Upload,
} from 'lucide-react'

import {
  Badge,
  Button,
  Card,
  Input,
  Popover,
  PopoverAnchor,
  PopoverContent,
  PopoverTrigger,
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@shared/components/ui'
import { useI18n, type TranslationKey } from '@shared/i18n'
import type { ProjectedAssetLibraryEntry } from '../assetLibraryProjection'
import {
  ASSET_LIBRARY_SORT_OPTIONS,
  DEFAULT_ASSET_LIBRARY_SORT_MODE,
  filterAssetLibraryEntryGroups,
  isAssetLibraryEntryOpenable,
  type AssetLibrarySortMode,
} from '../assetLibraryUi'

const SORT_LABEL_KEYS: Record<AssetLibrarySortMode, TranslationKey> = {
  name: 'assets.sortName',
  date: 'assets.sortDate',
}

const EXPORT_FORMATS = ['glb', 'obj', 'stl', 'ply'] as const
const ORIGINAL_EXPORT_FORMATS = ['original'] as const
export type AssetExportFormat = typeof EXPORT_FORMATS[number] | typeof ORIGINAL_EXPORT_FORMATS[number]

const EXPORT_FORMAT_I18N: Record<AssetExportFormat, TranslationKey> = {
  glb: 'assets.fmtGlb',
  obj: 'assets.fmtObj',
  stl: 'assets.fmtStl',
  ply: 'assets.fmtPly',
  original: 'assets.fmtOriginal',
}

function ExportFormatItems({
  onExport,
  onClose,
  formats = EXPORT_FORMATS,
  bundle = false,
}: {
  onExport: (format: AssetExportFormat) => void
  onClose: () => void
  formats?: readonly AssetExportFormat[]
  bundle?: boolean
}): JSX.Element {
  const { t } = useI18n()
  return (
    <div className="flex flex-col gap-0.5">
      {formats.map((format) => (
        <Button
          key={format}
          type="button"
          variant="ghost"
          size="sm"
          className="h-8 w-full justify-start gap-2.5 px-2 text-xs"
          onClick={() => { onExport(format); onClose() }}
        >
          <span className="font-mono text-[11px] font-semibold tabular-nums text-foreground">
            {format === 'original' ? t(bundle ? 'assets.fmtOriginalBundle' : 'assets.fmtOriginal') : `.${format}`}
          </span>
          {format !== 'original' && <span className="font-normal text-muted-foreground">{t(EXPORT_FORMAT_I18N[format])}</span>}
        </Button>
      ))}
    </div>
  )
}

type AssetCapability = NonNullable<ProjectedAssetLibraryEntry['capability']>

const CAPABILITY_LABEL_KEYS: Record<AssetCapability, TranslationKey> = {
  image: 'assets.capabilityImages',
  mesh: 'assets.capabilityMesh',
  'rigged-mesh': 'assets.capabilityRiggedMesh',
  'animation-motion': 'assets.capabilityAnimations',
  'landmarks-sidecar': 'assets.capabilityLandmarks',
  'generated-world': 'assets.capabilityGeneratedWorlds',
  'scene-manifest': 'assets.capabilitySceneManifests',
}

type AssetLibraryViewMode = 'grid' | 'list'

const ASSET_FAVORITES_STORAGE_KEY = 'polykit.asset-library.favorites'
const ASSET_VIEW_MODE_STORAGE_KEY = 'polykit.asset-library.view-mode'

function readFavoritePaths(): Set<string> {
  try {
    const raw = JSON.parse(localStorage.getItem(ASSET_FAVORITES_STORAGE_KEY) ?? '[]')
    return new Set(Array.isArray(raw) ? raw.filter((value): value is string => typeof value === 'string') : [])
  } catch {
    return new Set()
  }
}

function writeFavoritePaths(paths: Set<string>): void {
  try {
    localStorage.setItem(ASSET_FAVORITES_STORAGE_KEY, JSON.stringify([...paths]))
  } catch {
    // Favorites are a convenience; private browsing or a full quota must not block the library.
  }
}

function readAssetLibraryViewMode(): AssetLibraryViewMode {
  try {
    return localStorage.getItem(ASSET_VIEW_MODE_STORAGE_KEY) === 'list' ? 'list' : 'grid'
  } catch {
    return 'grid'
  }
}

function writeAssetLibraryViewMode(viewMode: AssetLibraryViewMode): void {
  try {
    localStorage.setItem(ASSET_VIEW_MODE_STORAGE_KEY, viewMode)
  } catch {
    // The view choice is a convenience and must not block the library.
  }
}

function AssetActionItems({
  onExport,
  canExport,
  onRename,
  onDelete,
  onClose,
}: {
  onExport: () => void
  canExport: boolean
  onRename: () => void
  onDelete: () => void
  onClose: () => void
}): JSX.Element {
  const { t } = useI18n()
  return (
    <div className="flex flex-col gap-0.5">
      {canExport && (
        <Button
          type="button"
          variant="ghost"
          size="sm"
          className="h-8 w-full justify-start gap-2 px-2 text-xs"
          onClick={onExport}
          role="menuitem"
        >
          <Download className="h-3.5 w-3.5" />
          {t('assets.export')}
        </Button>
      )}
      <Button
        type="button"
        variant="ghost"
        size="sm"
        className="h-8 w-full justify-start gap-2 px-2 text-xs"
        onClick={() => { onClose(); onRename() }}
        role="menuitem"
      >
        <Pencil className="h-3.5 w-3.5" />
        {t('assets.rename')}
      </Button>
      <Button
        type="button"
        variant="ghost"
        size="sm"
        className="h-8 w-full justify-start gap-2 px-2 text-xs text-destructive hover:bg-destructive/10 hover:text-destructive"
        onClick={() => { onClose(); onDelete() }}
        role="menuitem"
      >
        <Trash2 className="h-3.5 w-3.5" />
        {t('assets.delete')}
      </Button>
    </div>
  )
}

interface AssetLibrarySidebarProps {
  entries: ProjectedAssetLibraryEntry[]
  selectedEntryId: string | null
  /** Base URL prefix for relative asset URLs (e.g. the backend apiUrl). */
  thumbnailBase?: string
  loading: boolean
  opening: boolean
  importing: boolean
  error: string | null
  searchQuery: string
  sortMode: AssetLibrarySortMode
  collapsedSectionKeys: string[]
  onSelectEntry: (entryId: string) => void
  onSearchQueryChange: (value: string) => void
  onSortModeChange: (value: AssetLibrarySortMode) => void
  onToggleSection: (sectionKey: string) => void
  onOpenSelected: () => void
  onImport: () => void
  onExport: (workspacePaths: string[], format: AssetExportFormat) => void
  onRefresh: () => void
  onRename: (entry: ProjectedAssetLibraryEntry) => void
  onDelete: (workspacePaths: string[]) => void
}

function AssetCard({
  entry,
  selected,
  selectMode,
  checked,
  viewMode,
  onSelect,
  onToggle,
  onOpen,
  onExport,
  onRename,
  onDelete,
  favorite,
  onToggleFavorite,
  thumbnailBase,
}: {
  entry: ProjectedAssetLibraryEntry
  selected: boolean
  selectMode: boolean
  checked: boolean
  viewMode: AssetLibraryViewMode
  onSelect: () => void
  onToggle: () => void
  onOpen: () => void
  onExport: (format: AssetExportFormat) => void
  onRename: () => void
  onDelete: () => void
  favorite: boolean
  onToggleFavorite: () => void
  thumbnailBase?: string
}): JSX.Element {
  const { t } = useI18n()
  const openable = isAssetLibraryEntryOpenable(entry)
  const thumbnailUrl = entry.thumbnail ? `${thumbnailBase ?? ''}${entry.thumbnail}` : undefined
  const [thumbnailState, setThumbnailState] = useState<'loading' | 'loaded' | 'error'>(thumbnailUrl ? 'loading' : 'error')
  const [actionsAt, setActionsAt] = useState<{ x: number; y: number } | null>(null)
  const [exportMenuOpen, setExportMenuOpen] = useState(false)
  const cardRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    setThumbnailState(thumbnailUrl ? 'loading' : 'error')
  }, [thumbnailUrl])

  const openActionsAt = (x: number, y: number) => {
    if (selectMode) return
    onSelect()
    setExportMenuOpen(false)
    setActionsAt({ x, y })
  }

  const handleContextMenu = (event: React.MouseEvent<HTMLDivElement>) => {
    event.preventDefault()
    openActionsAt(event.clientX, event.clientY)
  }

  const isCardSelected = selectMode ? checked : selected

  const handleCardKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.target !== event.currentTarget) return
    if (event.key === 'ContextMenu' || (event.shiftKey && event.key === 'F10')) {
      event.preventDefault()
      const rect = cardRef.current?.getBoundingClientRect()
      openActionsAt(rect?.left ?? 0, rect?.bottom ?? 0)
      return
    }
    if (event.key !== 'Enter' && event.key !== ' ') return
    event.preventDefault()
    if (selectMode) onToggle()
    else onSelect()
  }

  const cardLayoutClass = 'flex-col gap-0'
  const mediaClass = viewMode === 'grid'
    ? 'h-24 w-full rounded-sm'
    : 'h-28 w-full rounded-sm'
  const detailsClass = viewMode === 'grid'
    ? 'px-0.5 pb-0.5 pt-2'
    : 'order-first px-1.5 py-2'

  return (
    <Popover open={actionsAt !== null} onOpenChange={(open) => {
      if (!open) {
        setActionsAt(null)
        setExportMenuOpen(false)
      }
    }}>
      <div
        ref={cardRef}
        role="button"
        tabIndex={0}
        aria-haspopup={selectMode ? undefined : 'menu'}
        aria-pressed={isCardSelected}
      aria-label={selectMode
        ? t('assets.toggleForDeletion', { name: entry.displayName })
        : t('assets.selectLibraryAsset', { name: entry.displayName })}
      onDoubleClick={selectMode ? undefined : onOpen}
      onClick={(event) => {
        if ((event.target as HTMLElement).closest('[data-asset-action]')) return
        if (selectMode) onToggle()
        else onSelect()
      }}
      onContextMenu={handleContextMenu}
      onKeyDown={handleCardKeyDown}
      className={`group relative flex ${cardLayoutClass} items-stretch overflow-hidden rounded-lg border p-2 text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background ${isCardSelected ? 'border-primary bg-primary/10 ring-1 ring-primary/20' : 'border-transparent bg-card/80 hover:bg-card'} ${!openable ? 'opacity-60' : ''}`}
    >
      {selectMode && (
        <span
          className={`pointer-events-none absolute left-1.5 top-1.5 z-10 flex h-5 w-5 items-center justify-center rounded-md bg-card/90 ${checked ? 'text-primary' : 'text-muted-foreground'}`}
          aria-hidden="true"
        >
          {checked ? <CheckSquare className="h-3.5 w-3.5" /> : <Square className="h-3.5 w-3.5" />}
        </span>
      )}
      <div className={`relative flex ${mediaClass} items-center justify-center overflow-hidden transition-colors group-hover:bg-muted/60 ${entry.capability === 'image' ? 'alpha-checker' : 'bg-muted/40'}`}>
        {(!thumbnailUrl || thumbnailState === 'error') && (
          entry.capability === 'image'
            ? <ImageIcon className="h-[22px] w-[22px] text-muted-foreground" strokeWidth={1.5} aria-hidden="true" />
            : <Box className="h-[22px] w-[22px] text-muted-foreground" strokeWidth={1.5} aria-hidden="true" />
        )}
        {thumbnailUrl && (
          <img
            src={thumbnailUrl}
            alt=""
            loading="lazy"
            onLoad={() => setThumbnailState('loaded')}
            onError={() => setThumbnailState('error')}
            className={`absolute inset-0 h-full w-full object-contain brightness-[0.96] contrast-[1.03] saturate-[0.96] transition-[filter,opacity] duration-200 ${thumbnailState === 'error' ? 'hidden' : ''}`}
          />
        )}
        <div className="pointer-events-none absolute inset-0 bg-gradient-to-br from-white/[0.025] via-transparent to-black/[0.1]" aria-hidden="true" />
        <Button
          type="button"
          variant="ghost"
          size="icon"
          data-asset-action
          className={`absolute right-1.5 top-1.5 z-10 h-7 w-7 rounded-md bg-transparent p-0 transition-opacity hover:bg-transparent focus-visible:opacity-100 ${favorite ? 'text-primary opacity-100 hover:text-primary' : `text-muted-foreground hover:text-foreground ${isCardSelected ? 'opacity-100' : 'opacity-0 group-hover:opacity-100'}`}`}
          onClick={(event) => { event.stopPropagation(); onToggleFavorite() }}
          aria-label={t(favorite ? 'assets.unfavorite' : 'assets.favorite')}
          aria-pressed={favorite}
          title={t(favorite ? 'assets.unfavorite' : 'assets.favorite')}
        >
          <Star className="h-3.5 w-3.5" fill={favorite ? 'currentColor' : 'none'} strokeWidth={1.8} />
        </Button>
      </div>
      <div className={`flex min-w-0 flex-1 flex-col ${detailsClass}`}>
        <span className="truncate text-[11px] font-medium text-foreground">{entry.displayName}</span>
      </div>
      </div>
      <PopoverAnchor asChild>
        <span
          aria-hidden="true"
          className="pointer-events-none fixed left-0 top-0 h-px w-px"
          style={{ left: actionsAt?.x ?? -100, top: actionsAt?.y ?? -100 }}
        />
      </PopoverAnchor>
      {actionsAt && (
        <PopoverContent align="start" side="bottom" sideOffset={4} className={exportMenuOpen ? 'w-44 p-1.5' : 'w-36 p-1.5'} role="menu">
          {exportMenuOpen ? (
            <>
              <Button
                type="button"
                variant="ghost"
                size="sm"
                className="mb-0.5 h-7 w-full justify-start gap-1.5 px-2 text-[11px] text-muted-foreground"
                onClick={() => setExportMenuOpen(false)}
              >
                <ChevronRight className="h-3 w-3 rotate-180" />
                {t('common.back')}
              </Button>
              <ExportFormatItems
                onExport={onExport}
                onClose={() => {
                  setActionsAt(null)
                  setExportMenuOpen(false)
                }}
                formats={entry.capability === 'image' ? ORIGINAL_EXPORT_FORMATS : EXPORT_FORMATS}
              />
            </>
          ) : (
            <AssetActionItems
              onExport={() => setExportMenuOpen(true)}
              canExport={entry.capability === 'image' || entry.capability === 'mesh' || entry.capability === 'rigged-mesh'}
              onRename={onRename}
              onDelete={onDelete}
              onClose={() => setActionsAt(null)}
            />
          )}
        </PopoverContent>
      )}
    </Popover>
  )
}

export default function AssetLibrarySidebar({
  entries,
  selectedEntryId,
  thumbnailBase,
  loading,
  opening,
  importing,
  error,
  searchQuery,
  sortMode,
  collapsedSectionKeys,
  onSelectEntry,
  onSearchQueryChange,
  onSortModeChange,
  onToggleSection,
  onOpenSelected,
  onImport,
  onExport,
  onRefresh,
  onRename,
  onDelete,
}: AssetLibrarySidebarProps): JSX.Element {
  const { t } = useI18n()
  const [selectMode, setSelectMode] = useState(false)
  const [selectedPaths, setSelectedPaths] = useState<Set<string>>(() => new Set())
  const [favoritePaths, setFavoritePaths] = useState<Set<string>>(readFavoritePaths)
  const [viewMode, setViewMode] = useState<AssetLibraryViewMode>(readAssetLibraryViewMode)
  const [exportMenuOpen, setExportMenuOpen] = useState(false)
  const entryGroups = filterAssetLibraryEntryGroups(entries, searchQuery, sortMode)
  const normalizedSearchQuery = searchQuery.trim()

  useEffect(() => {
    const availablePaths = new Set(entries.map((entry) => entry.workspacePath))
    setSelectedPaths((current) => {
      const next = new Set([...current].filter((path) => availablePaths.has(path)))
      return next.size === current.size ? current : next
    })
  }, [entries])

  const leaveSelectMode = () => {
    setSelectMode(false)
    setSelectedPaths(new Set())
  }

  const toggleFavorite = (workspacePath: string) => {
    setFavoritePaths((current) => {
      const next = new Set(current)
      if (next.has(workspacePath)) next.delete(workspacePath)
      else next.add(workspacePath)
      writeFavoritePaths(next)
      return next
    })
  }

  const changeViewMode = (nextViewMode: AssetLibraryViewMode) => {
    setViewMode(nextViewMode)
    writeAssetLibraryViewMode(nextViewMode)
  }

  const selectedEntry = entries.find((entry) => entry.id === selectedEntryId) ?? null
  const exportablePaths = new Set(entries
    .filter((entry) => entry.capability === 'image' || entry.capability === 'mesh' || entry.capability === 'rigged-mesh')
    .map((entry) => entry.workspacePath))
  const exportTargets = selectMode
    ? [...selectedPaths].filter((path) => exportablePaths.has(path))
    : selectedEntry && exportablePaths.has(selectedEntry.workspacePath) ? [selectedEntry.workspacePath] : []
  const exportTargetHasImage = exportTargets.some((path) => entries.find((entry) => entry.workspacePath === path)?.capability === 'image')
  const exportFormats: readonly AssetExportFormat[] = exportTargetHasImage ? ORIGINAL_EXPORT_FORMATS : EXPORT_FORMATS
  const exportLabel = selectMode && selectedPaths.size > 0
    ? t('assets.exportSelected', { count: selectedPaths.size })
    : t('assets.export')

  return (
    <div className="flex h-full min-h-0 flex-col overflow-hidden rounded-lg border border-divider bg-card">
      <div className="shrink-0 bg-card/45 px-3 py-3">
        <div className="flex items-center gap-2">
          <div className="relative min-w-0 flex-1">
            <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
            <Input
              id="asset-library-search"
              type="search"
              value={searchQuery}
              onChange={(event) => onSearchQueryChange(event.target.value)}
              placeholder={t('assets.searchPlaceholder')}
              aria-label={t('assets.search')}
              className="h-9 rounded-md border-border/70 bg-card pl-8 pr-2 text-xs"
            />
          </div>
          <Button
            type="button"
            size="sm"
            className="h-9 shrink-0 gap-1.5 rounded-md px-3"
            onClick={onImport}
            disabled={importing || loading || opening}
            title={t('assets.import')}
          >
            {importing ? <RefreshCw className="h-3.5 w-3.5 animate-spin" /> : <Upload className="h-3.5 w-3.5" />}
            {importing ? t('assets.importing') : t('assets.import')}
          </Button>
        </div>

        <div className="mt-2 flex items-center gap-1">
          <Button
            type="button"
            variant={selectMode ? 'secondary' : 'ghost'}
            size="icon"
            className="h-8 w-8"
            onClick={() => {
              if (selectMode) leaveSelectMode()
              else setSelectMode(true)
            }}
            aria-pressed={selectMode}
            aria-label={selectMode ? t('assets.done') : t('assets.select')}
            title={selectMode ? t('assets.done') : t('assets.select')}
          >
            <CheckSquare className="h-4 w-4" />
          </Button>
          <Popover open={exportMenuOpen} onOpenChange={setExportMenuOpen}>
            <PopoverTrigger asChild>
              <Button
                type="button"
                variant={exportMenuOpen ? 'secondary' : 'ghost'}
                size="sm"
                className="h-8 gap-1.5 px-2 text-[11px]"
                disabled={exportTargets.length === 0}
                title={exportLabel}
              >
                <Download className="h-3.5 w-3.5" />
                <span className="max-w-[112px] truncate">{exportLabel}</span>
                <ChevronDown className="h-3 w-3 shrink-0" />
              </Button>
            </PopoverTrigger>
            <PopoverContent align="start" className="w-44 p-1.5">
              <ExportFormatItems
                onExport={(format) => onExport(exportTargets, format)}
                onClose={() => setExportMenuOpen(false)}
                formats={exportFormats}
                bundle={exportTargets.length > 1}
              />
            </PopoverContent>
          </Popover>
          <Select value={sortMode} onValueChange={(value) => onSortModeChange(value as AssetLibrarySortMode)}>
            <SelectTrigger className="h-8 w-[94px] gap-1.5 px-2 text-[11px]" aria-label={t('assets.sort')}>
              <ArrowDownUp className="h-3.5 w-3.5 shrink-0 text-muted-foreground" aria-hidden="true" />
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {ASSET_LIBRARY_SORT_OPTIONS.map((option) => (
                <SelectItem key={option.value} value={option.value}>{t(SORT_LABEL_KEYS[option.value])}</SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Button
            type="button"
            variant="ghost"
            size="icon"
            className="h-8 w-8"
            onClick={onRefresh}
            disabled={loading || opening}
            title={t('assets.refresh')}
            aria-label={t('assets.refresh')}
          >
            <RefreshCw className={`h-3.5 w-3.5 ${loading ? 'animate-spin' : ''}`} />
          </Button>
        </div>
      </div>

      {loading ? (
        <p role="status" className="px-4 py-4 text-xs text-muted-foreground">{t('assets.loading')}</p>
      ) : entryGroups.length === 0 && !normalizedSearchQuery ? (
        <p role="status" className="px-4 py-4 text-xs text-muted-foreground">{t('assets.noAssets')}</p>
      ) : entryGroups.length === 0 ? (
        <p role="status" className="px-4 py-4 text-xs text-muted-foreground">{t('assets.noMatch', { query: normalizedSearchQuery })}</p>
      ) : (
        <div className="min-h-0 flex-1 overflow-y-auto bg-muted/35 pb-4">
          {entryGroups.map((group) => {
            const collapsed = collapsedSectionKeys.includes(group.sectionKey)
            return (
              <section key={group.sectionKey} role="group" aria-label={t(CAPABILITY_LABEL_KEYS[group.capability])}>
                <button
                  type="button"
                  className="sticky top-0 z-10 flex w-full items-center gap-2 bg-card px-3 pb-2 pt-3 text-left hover:bg-muted/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"
                  onClick={() => onToggleSection(group.sectionKey)}
                  aria-expanded={!collapsed}
                >
                  <ChevronRight className={`h-3 w-3 shrink-0 text-muted-foreground transition-transform ${collapsed ? '' : 'rotate-90'}`} />
                  <h3 className="text-[11px] font-semibold text-foreground">{t(CAPABILITY_LABEL_KEYS[group.capability])}</h3>
                  <Badge variant="outline" className="ml-auto h-5 px-1.5 font-mono text-[10px] text-muted-foreground">{group.entries.length}</Badge>
                </button>
                {!collapsed && (
                  <div className={viewMode === 'grid' ? 'grid grid-cols-2 gap-2 px-3 pt-1' : 'flex flex-col gap-2 px-3 pt-1'}>
                    {group.entries.map((entry) => (
                      <AssetCard
                        key={entry.id}
                        entry={entry}
                        selected={entry.id === selectedEntryId}
                        selectMode={selectMode}
                        checked={selectedPaths.has(entry.workspacePath)}
                        viewMode={viewMode}
                        onSelect={() => onSelectEntry(entry.id)}
                        onToggle={() => {
                          setSelectedPaths((current) => {
                            const next = new Set(current)
                            if (next.has(entry.workspacePath)) next.delete(entry.workspacePath)
                            else next.add(entry.workspacePath)
                            return next
                          })
                        }}
                        onOpen={onOpenSelected}
                        onExport={(format) => onExport([entry.workspacePath], format)}
                        onRename={() => onRename(entry)}
                        onDelete={() => onDelete([entry.workspacePath])}
                        favorite={favoritePaths.has(entry.workspacePath)}
                        onToggleFavorite={() => toggleFavorite(entry.workspacePath)}
                        thumbnailBase={thumbnailBase}
                      />
                    ))}
                  </div>
                )}
              </section>
            )
          })}
        </div>
      )}

      {(error || selectMode) && (
        <div className="flex shrink-0 flex-col gap-2.5 bg-card/45 px-4 py-3">
          {error && (
            <Card className="rounded-md bg-muted/20 px-3.5 py-2.5 shadow-none">
              <p role="alert" className="text-[11px] text-amber-400">{error}</p>
            </Card>
          )}
          {selectMode && (
            <div className="flex items-center gap-2">
              <Button
                type="button"
                variant="destructive"
                size="sm"
                className="flex-1 gap-1.5"
                onClick={() => selectedPaths.size > 0 && onDelete([...selectedPaths])}
                disabled={selectedPaths.size === 0}
              >
                <Trash2 className="h-3.5 w-3.5" />
                {t('assets.deleteSelected', { count: selectedPaths.size })}
              </Button>
              <Button type="button" variant="outline" size="sm" onClick={leaveSelectMode}>{t('common.cancel')}</Button>
            </div>
          )}
        </div>
      )}

      <div className="flex shrink-0 items-center gap-2 bg-card/45 px-3 py-2">
        <div className="flex items-center gap-0.5 rounded-md bg-muted/20 p-0.5" role="group" aria-label={t('assets.viewMode')}>
          <Button
            type="button"
            variant={viewMode === 'list' ? 'secondary' : 'ghost'}
            size="icon"
            className="h-7 w-7"
            onClick={() => changeViewMode('list')}
            aria-pressed={viewMode === 'list'}
            aria-label={t('assets.listView')}
            title={t('assets.listView')}
          >
            <List className="h-3.5 w-3.5" />
          </Button>
          <Button
            type="button"
            variant={viewMode === 'grid' ? 'secondary' : 'ghost'}
            size="icon"
            className="h-7 w-7"
            onClick={() => changeViewMode('grid')}
            aria-pressed={viewMode === 'grid'}
            aria-label={t('assets.gridView')}
            title={t('assets.gridView')}
          >
            <LayoutGrid className="h-3.5 w-3.5" />
          </Button>
        </div>
        <span className="ml-auto font-mono text-[10px] tabular-nums text-muted-foreground">{entries.length}</span>
      </div>
    </div>
  )
}

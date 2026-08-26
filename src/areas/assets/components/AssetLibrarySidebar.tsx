import { useEffect, useRef, useState } from 'react'
import {
  Box,
  Check,
  ChevronRight,
  Layers3,
  MoreHorizontal,
  Pencil,
  RefreshCw,
  Search,
  Trash2,
} from 'lucide-react'

import {
  Badge,
  Button,
  Card,
  Input,
  Label,
  Popover,
  PopoverContent,
  PopoverTrigger,
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@shared/components/ui'
import { useI18n, type TranslationKey } from '@shared/i18n'
import AssetPreview3D from './AssetPreview3D'
import type { ProjectedAssetLibraryEntry } from '../assetLibraryProjection'
import {
  ASSET_LIBRARY_SORT_OPTIONS,
  DEFAULT_ASSET_LIBRARY_SORT_MODE,
  filterAssetLibraryEntryGroups,
  isAssetLibraryEntryOpenable,
  type AssetLibrarySortMode,
} from '../assetLibraryUi'

const SORT_LABEL_KEYS: Record<AssetLibrarySortMode, TranslationKey> = {
  type: 'assets.sortType',
  name: 'assets.sortName',
  date: 'assets.sortDate',
}

type AssetCapability = NonNullable<ProjectedAssetLibraryEntry['capability']>

const CAPABILITY_LABEL_KEYS: Record<AssetCapability, TranslationKey> = {
  mesh: 'assets.capabilityMesh',
  'rigged-mesh': 'assets.capabilityRiggedMesh',
  'animation-motion': 'assets.capabilityAnimations',
  'landmarks-sidecar': 'assets.capabilityLandmarks',
  'generated-world': 'assets.capabilityGeneratedWorlds',
  'scene-manifest': 'assets.capabilitySceneManifests',
}

interface AssetLibrarySidebarProps {
  entries: ProjectedAssetLibraryEntry[]
  selectedEntryId: string | null
  /** Base URL prefix for relative asset URLs (e.g. the backend apiUrl). */
  thumbnailBase?: string
  loading: boolean
  opening: boolean
  error: string | null
  searchQuery: string
  sortMode: AssetLibrarySortMode
  collapsedSectionKeys: string[]
  onSelectEntry: (entryId: string) => void
  onSearchQueryChange: (value: string) => void
  onSortModeChange: (value: AssetLibrarySortMode) => void
  onToggleSection: (sectionKey: string) => void
  onOpenSelected: () => void
  onRefresh: () => void
  onRename: (entry: ProjectedAssetLibraryEntry) => void
  onDelete: (workspacePaths: string[]) => void
}

function AssetCard({
  entry,
  selected,
  selectMode,
  checked,
  onSelect,
  onToggle,
  onOpen,
  thumbnailBase,
}: {
  entry: ProjectedAssetLibraryEntry
  selected: boolean
  selectMode: boolean
  checked: boolean
  onSelect: () => void
  onToggle: () => void
  onOpen: () => void
  thumbnailBase?: string
}): JSX.Element {
  const { t } = useI18n()
  const openable = isAssetLibraryEntryOpenable(entry)
  const badge = entry.capability ? t(CAPABILITY_LABEL_KEYS[entry.capability]) : entry.state.replace(/-/g, ' ')
  const thumbnailUrl = entry.thumbnail ? `${thumbnailBase ?? ''}${entry.thumbnail}` : undefined
  const previewUrl = entry.preview ? `${thumbnailBase ?? ''}${entry.preview}` : undefined
  const [previewHovered, setPreviewHovered] = useState(false)
  const hoverTimer = useRef<number | null>(null)

  useEffect(() => () => {
    if (hoverTimer.current !== null) window.clearTimeout(hoverTimer.current)
  }, [])

  const startPreviewHover = () => {
    if (hoverTimer.current !== null) window.clearTimeout(hoverTimer.current)
    hoverTimer.current = window.setTimeout(() => setPreviewHovered(true), 180)
  }

  const stopPreviewHover = () => {
    if (hoverTimer.current !== null) {
      window.clearTimeout(hoverTimer.current)
      hoverTimer.current = null
    }
    setPreviewHovered(false)
  }

  return (
    <button
      type="button"
      aria-pressed={selectMode ? checked : selected}
      aria-label={selectMode
        ? t('assets.toggleForDeletion', { name: entry.displayName })
        : t('assets.selectLibraryAsset', { name: entry.displayName })}
      onDoubleClick={selectMode ? undefined : onOpen}
      onClick={selectMode ? onToggle : onSelect}
      onMouseEnter={startPreviewHover}
      onMouseLeave={stopPreviewHover}
      onFocus={startPreviewHover}
      onBlur={stopPreviewHover}
      className={`group flex flex-col items-stretch gap-1.5 rounded-lg border p-2 text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background ${selected ? 'border-primary/50 bg-primary/10' : 'border-border bg-card hover:bg-muted/40'} ${!openable ? 'opacity-60' : ''}`}
    >
      <div className="relative flex h-16 items-center justify-center overflow-hidden rounded-md border border-border bg-muted/40 transition-colors group-hover:bg-muted/60">
        {!(previewUrl && previewHovered) && (
          <Box className="h-[22px] w-[22px] text-muted-foreground" strokeWidth={1.5} aria-hidden="true" />
        )}
        {thumbnailUrl && !(previewUrl && previewHovered) && (
          <img
            src={thumbnailUrl}
            alt=""
            loading="lazy"
            onError={(event) => { event.currentTarget.style.display = 'none' }}
            className="absolute inset-0 h-full w-full object-cover"
          />
        )}
        {previewUrl && previewHovered && <AssetPreview3D url={previewUrl} className="absolute inset-0" />}
        {selectMode ? (
          <span
            className={`absolute left-1.5 top-1.5 flex h-4 w-4 items-center justify-center rounded border transition-colors ${checked ? 'border-primary bg-primary text-primary-foreground' : 'border-border bg-background/85 text-transparent'}`}
            aria-hidden="true"
          >
            <Check className="h-2.5 w-2.5" strokeWidth={3} />
          </span>
        ) : selected ? (
          <Check className="absolute right-1.5 top-1.5 h-3.5 w-3.5 text-primary" strokeWidth={2.5} aria-hidden="true" />
        ) : null}
      </div>
      <div className="flex min-w-0 flex-col">
        <span className="truncate text-[11px] font-medium text-foreground">{entry.displayName}</span>
        <span className="truncate text-[10px] uppercase tracking-wider text-muted-foreground">{badge}</span>
      </div>
    </button>
  )
}

export default function AssetLibrarySidebar({
  entries,
  selectedEntryId,
  thumbnailBase,
  loading,
  opening,
  error,
  searchQuery,
  sortMode,
  collapsedSectionKeys,
  onSelectEntry,
  onSearchQueryChange,
  onSortModeChange,
  onToggleSection,
  onOpenSelected,
  onRefresh,
  onRename,
  onDelete,
}: AssetLibrarySidebarProps): JSX.Element {
  const { t } = useI18n()
  const [selectMode, setSelectMode] = useState(false)
  const [selectedPaths, setSelectedPaths] = useState<Set<string>>(() => new Set())
  const [actionsOpen, setActionsOpen] = useState(false)
  const defaultSortApplied = useRef(false)

  const entryGroups = filterAssetLibraryEntryGroups(entries, searchQuery, sortMode)
  const visibleEntryIds = new Set(entryGroups.flatMap((group) => group.entries.map((entry) => entry.id)))
  const selectedEntry = selectedEntryId && visibleEntryIds.has(selectedEntryId)
    ? entries.find((entry) => entry.id === selectedEntryId) ?? null
    : null
  const normalizedSearchQuery = searchQuery.trim()

  useEffect(() => {
    if (defaultSortApplied.current) return
    defaultSortApplied.current = true
    if (sortMode === 'type') onSortModeChange(DEFAULT_ASSET_LIBRARY_SORT_MODE)
  }, [onSortModeChange, sortMode])

  useEffect(() => {
    setActionsOpen(false)
  }, [selectedEntryId, selectMode])

  const leaveSelectMode = () => {
    setSelectMode(false)
    setSelectedPaths(new Set())
  }

  const renameSelected = () => {
    if (!selectedEntry) return
    setActionsOpen(false)
    onRename(selectedEntry)
  }

  const deleteSelected = () => {
    if (!selectedEntry) return
    setActionsOpen(false)
    onDelete([selectedEntry.workspacePath])
  }

  return (
    <div className="flex h-full min-h-0 flex-col bg-card">
      <div className="flex shrink-0 items-center justify-between gap-2 border-b border-border px-4 pb-3 pt-4">
        <div className="flex min-w-0 items-center gap-2">
          <Layers3 className="h-4 w-4 shrink-0 text-muted-foreground" strokeWidth={1.75} aria-hidden="true" />
          <h2 className="truncate text-xs font-semibold text-foreground">{t('assets.title')}</h2>
        </div>
        <div className="flex items-center gap-1.5">
          <Button
            type="button"
            variant={selectMode ? 'secondary' : 'outline'}
            size="sm"
            className="h-8 px-2.5"
            onClick={() => {
              if (selectMode) leaveSelectMode()
              else setSelectMode(true)
            }}
            aria-pressed={selectMode}
          >
            {selectMode ? t('assets.done') : t('assets.select')}
          </Button>
          <Button
            type="button"
            variant="outline"
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

      <div className="flex shrink-0 items-end gap-2 border-b border-border px-4 py-3.5">
        <div className="flex min-w-0 flex-1 flex-col gap-1.5">
          <Label htmlFor="asset-library-search" className="text-[11px] text-muted-foreground">{t('assets.search')}</Label>
          <div className="relative">
            <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
            <Input
              id="asset-library-search"
              type="search"
              value={searchQuery}
              onChange={(event) => onSearchQueryChange(event.target.value)}
              placeholder={t('assets.searchPlaceholder')}
              className="h-9 pl-8 text-xs"
            />
          </div>
        </div>
        <div className="flex w-28 shrink-0 flex-col gap-1.5">
          <Label className="text-[11px] text-muted-foreground">{t('assets.sort')}</Label>
          <Select value={sortMode} onValueChange={(value) => onSortModeChange(value as AssetLibrarySortMode)}>
            <SelectTrigger className="h-9 text-xs" aria-label={t('assets.sort')}>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {ASSET_LIBRARY_SORT_OPTIONS.map((option) => (
                <SelectItem key={option.value} value={option.value}>{t(SORT_LABEL_KEYS[option.value])}</SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      </div>

      {loading ? (
        <p role="status" className="px-4 py-4 text-xs text-muted-foreground">{t('assets.loading')}</p>
      ) : entryGroups.length === 0 && !normalizedSearchQuery ? (
        <p role="status" className="px-4 py-4 text-xs text-muted-foreground">{t('assets.noAssets')}</p>
      ) : entryGroups.length === 0 ? (
        <p role="status" className="px-4 py-4 text-xs text-muted-foreground">{t('assets.noMatch', { query: normalizedSearchQuery })}</p>
      ) : (
        <div className="min-h-0 flex-1 overflow-y-auto pb-4">
          {entryGroups.map((group) => {
            const collapsed = collapsedSectionKeys.includes(group.sectionKey)
            return (
              <section key={group.sectionKey} role="group" aria-label={t(CAPABILITY_LABEL_KEYS[group.capability])}>
                <button
                  type="button"
                  className="sticky top-0 z-10 flex w-full items-center gap-2 bg-card/95 px-4 pb-2 pt-3 text-left backdrop-blur-sm hover:bg-muted/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"
                  onClick={() => onToggleSection(group.sectionKey)}
                  aria-expanded={!collapsed}
                >
                  <ChevronRight className={`h-3 w-3 shrink-0 text-muted-foreground transition-transform ${collapsed ? '' : 'rotate-90'}`} />
                  <h3 className="text-[11px] font-semibold text-foreground">{t(CAPABILITY_LABEL_KEYS[group.capability])}</h3>
                  <Badge variant="outline" className="ml-auto h-5 px-1.5 font-mono text-[10px] text-muted-foreground">{group.entries.length}</Badge>
                </button>
                {!collapsed && (
                  <div className="grid grid-cols-2 gap-2 px-4 pt-1">
                    {group.entries.map((entry) => (
                      <AssetCard
                        key={entry.id}
                        entry={entry}
                        selected={entry.id === selectedEntryId}
                        selectMode={selectMode}
                        checked={selectedPaths.has(entry.workspacePath)}
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

      <div className="flex shrink-0 flex-col gap-2.5 border-t border-border px-4 py-4">
        {error && (
          <Card className="rounded-md bg-muted/20 px-3.5 py-2.5 shadow-none">
            <p role="alert" className="text-[11px] text-amber-400">{error}</p>
          </Card>
        )}
        {selectMode ? (
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
        ) : (
          <div className="flex justify-end">
            <Popover open={actionsOpen} onOpenChange={setActionsOpen}>
              <PopoverTrigger asChild>
                <Button
                  type="button"
                  variant="outline"
                  size="icon"
                  className="h-9 w-9 shrink-0"
                  disabled={!selectedEntry}
                  aria-label={t('assets.moreAssetActions')}
                  aria-haspopup="menu"
                  title={t('assets.moreActions')}
                >
                  <MoreHorizontal className="h-4 w-4" />
                </Button>
              </PopoverTrigger>
              <PopoverContent align="end" side="top" className="w-40 p-1.5" role="menu">
                <div className="flex flex-col gap-0.5">
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    className="h-8 w-full justify-start gap-2 px-2 text-xs"
                    onClick={renameSelected}
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
                    onClick={deleteSelected}
                    role="menuitem"
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                    {t('assets.delete')}
                  </Button>
                </div>
              </PopoverContent>
            </Popover>
          </div>
        )}
      </div>
    </div>
  )
}

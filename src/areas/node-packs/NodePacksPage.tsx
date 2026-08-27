import { useEffect, useMemo, useRef, useState } from 'react'
import {
  Box,
  CheckCircle2,
  CircleAlert,
  Download,
  FolderOpen,
  Github,
  LoaderCircle,
  Search,
  Sparkles,
  Trash2,
  X,
} from 'lucide-react'

import {
  Badge,
  Button,
  Card,
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  Input,
} from '@shared/components/ui'
import { useNodePacksStore } from '@shared/stores/nodePacksStore'
import type { AnyNodePack, ModelNodePack } from '@shared/types/runtime.d'
import { NodePackCard } from './components/NodePackCard'
import type { NodePackNode } from './components/NodePackCard'
import { NodePackDrawer } from './components/NodePackDrawer'
import { getNodeDownloadId } from './components/nodePackShared'
import { useNodePacksI18n } from './i18n'
import { localizedNodePackName, nodePackSearchText } from './nodePackI18n'
import { formatModelName } from './modelNames'

// ─── Filters & sorts ──────────────────────────────────────────────────────────

type FilterId = 'all' | 'process' | 'model' | 'official'
const FILTERS: FilterId[] = ['all', 'process', 'model', 'official']

// ─── Page ─────────────────────────────────────────────────────────────────────

export default function NodePacksPage(): JSX.Element {
  const { language, t } = useNodePacksI18n()
  const modelNodePacks = useNodePacksStore((s) => s.modelNodePacks)
  const processNodePacks = useNodePacksStore((s) => s.processNodePacks)
  const extLoading = useNodePacksStore((s) => s.loading)
  const installProgress = useNodePacksStore((s) => s.installProgress)
  const installError = useNodePacksStore((s) => s.installError)
  const loadErrors = useNodePacksStore((s) => s.loadErrors)
  const loadNodePacks = useNodePacksStore((s) => s.loadNodePacks)
  const installFromGH = useNodePacksStore((s) => s.installFromGitHub)
  const installFromLocal = useNodePacksStore((s) => s.installFromLocal)
  const uninstallExt = useNodePacksStore((s) => s.uninstall)
  const reloadNodePacks = useNodePacksStore((s) => s.reload)
  const clearInstall = useNodePacksStore((s) => s.clearInstallState)

  const allNodePacks: AnyNodePack[] = useMemo(
    () => [...modelNodePacks, ...processNodePacks],
    [modelNodePacks, processNodePacks],
  )

  const [installedVariantIds, setInstalledVariantIds] = useState<string[]>([])
  const [downloading, setDownloading] = useState<Record<string, {
    percent: number
    file?: string
    fileIndex?: number
    totalFiles?: number
    status?: string
    bytesDownloaded?: number
    totalBytes?: number
    stalledSeconds?: number
    paused?: boolean
  }>>({})

  const [uninstallTarget, setUninstallTarget] = useState<string | null>(null)
  const [uninstallError, setUninstallError] = useState<string | null>(null)
  const [modelsToDelete, setModelsToDelete] = useState<Set<string>>(new Set())

  const [search, setSearch] = useState('')
  const [filter, setFilter] = useState<FilterId>('all')
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const searchRef = useRef<HTMLInputElement>(null)

  const [showGHForm, setShowGHForm] = useState(false)
  const [ghUrl, setGhUrl] = useState('')
  const [ghErr, setGhErr] = useState<string | null>(null)
  const [downloadErrors, setDownloadErrors] = useState<Record<string, string>>({})
  const [isWeb, setIsWeb] = useState(false)
  const [initialLoadReady, setInitialLoadReady] = useState(false)

  async function refreshInstalledIds(exts: ModelNodePack[]) {
    const ids: string[] = []
    const checked = new Map<string, boolean>()
    for (const ext of exts) {
      for (const node of ext.nodes) {
        if (!node.hfRepo) continue
        const fullId = `${ext.id}/${node.id}`
        const downloadId = getNodeDownloadId(ext, node)
        const ok = checked.has(downloadId)
          ? checked.get(downloadId) === true
          : await window.polykit.model.isDownloaded(downloadId, node.downloadCheck)
        checked.set(downloadId, ok)
        if (ok) ids.push(fullId)
      }
    }
    setInstalledVariantIds(ids)
  }

  useEffect(() => {
    window.polykit.app.info().then((info) => setIsWeb(info.platform === 'web')).catch(() => {})
    let cancelled = false
    async function initializePage() {
      try {
        await loadNodePacks()
        if (cancelled) return

        const exts = useNodePacksStore.getState().modelNodePacks
        const active = await window.polykit.model.activeDownloads()
        if (cancelled) return
        if (active.length > 0) {
          setDownloading((prev) => {
            const next = { ...prev }
            for (const { modelId, ...progress } of active) if (!next[modelId]) next[modelId] = progress
            return next
          })
        }

        // Installed state changes card controls and status labels. Wait for it
        // before revealing the page so cards do not resize after first paint.
        await refreshInstalledIds(exts)
      } catch {
        // The store keeps its empty/error state; the page should still become
        // usable if an optional status check is unavailable.
      } finally {
        if (!cancelled) setInitialLoadReady(true)
      }
    }
    void initializePage()
    window.polykit.model.onProgress(({ modelId: id, percent, file, fileIndex, totalFiles, status, bytesDownloaded, totalBytes, stalledSeconds, paused, cancelled }) => {
      if (cancelled) {
        setDownloading((prev) => { const next = { ...prev }; delete next[id]; return next })
        return
      }
      setDownloading((prev) => {
        const current = prev[id]
        return {
          ...prev,
          [id]: {
            percent: paused ? (current?.percent ?? percent) : percent,
            file: file ?? current?.file,
            fileIndex: fileIndex ?? current?.fileIndex,
            totalFiles: totalFiles ?? current?.totalFiles,
            status,
            bytesDownloaded: bytesDownloaded ?? current?.bytesDownloaded,
            totalBytes: totalBytes ?? current?.totalBytes,
            stalledSeconds: stalledSeconds ?? current?.stalledSeconds,
            paused,
          },
        }
      })
      if (percent === 100) {
        const exts = useNodePacksStore.getState().modelNodePacks
        refreshInstalledIds(exts).then(() => {
          setDownloading((prev) => { const next = { ...prev }; delete next[id]; return next })
        })
      }
    })
    return () => {
      cancelled = true
      window.polykit.model.offProgress()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- register once on mount
  }, [])

  useEffect(() => {
    if (installError) setGhErr(installError)
  }, [installError])

  useEffect(() => {
    const handler = (event: KeyboardEvent) => {
      if (event.key !== '/') return
      const el = document.activeElement
      if (el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement) return
      event.preventDefault()
      searchRef.current?.focus()
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [])

  function handleInstallNode(node: NodePackNode, fullId: string, downloadId = fullId) {
    if (!node.hfRepo) return
    setDownloadErrors((prev) => {
      if (!(downloadId in prev)) return prev
      const next = { ...prev }
      delete next[downloadId]
      return next
    })
    setDownloading((prev) => ({ ...prev, [downloadId]: { ...(prev[downloadId] ?? { percent: 0 }), paused: false, status: t('nodePacks.starting') } }))
    void window.polykit.model.download(node.hfRepo, downloadId, node.hfSkipPrefixes, node.hfIncludePrefixes).then((result) => {
      if (!result.success && !result.paused && !result.cancelled) {
        setDownloadErrors((prev) => ({ ...prev, [downloadId]: result.error ?? t('nodePacks.downloadFailed') }))
        setDownloading((prev) => { const next = { ...prev }; delete next[downloadId]; return next })
      }
    }).catch((error: unknown) => {
      const message = error instanceof Error ? error.message : typeof error === 'string' ? error : t('nodePacks.downloadFailed')
      setDownloadErrors((prev) => ({ ...prev, [downloadId]: message }))
      setDownloading((prev) => { const next = { ...prev }; delete next[downloadId]; return next })
    })
  }

  function handleInstallAll(ext: AnyNodePack) {
    if (ext.type !== 'model') return
    const started = new Set(Object.keys(downloading))
    for (const node of ext.nodes) {
      if (!node.hfRepo) continue
      const fullId = `${ext.id}/${node.id}`
      const downloadId = getNodeDownloadId(ext, node)
      if (installedVariantIds.includes(fullId) || started.has(downloadId)) continue
      started.add(downloadId)
      handleInstallNode(node, fullId, downloadId)
    }
  }

  async function handlePauseDownload(downloadId: string) {
    setDownloading((prev) => prev[downloadId] ? ({ ...prev, [downloadId]: { ...prev[downloadId], paused: true, status: t('nodePacks.pausing') } }) : prev)
    try {
      const result = await window.polykit.model.pauseDownload(downloadId)
      if (!result.paused) {
        setDownloading((prev) => prev[downloadId] ? ({ ...prev, [downloadId]: { ...prev[downloadId], paused: false, status: t('nodePacks.progressDownloading', { percent: prev[downloadId].percent }) } }) : prev)
        setDownloadErrors((prev) => ({ ...prev, [downloadId]: t('nodePacks.downloadFailed') }))
      }
    } catch (error: unknown) {
      const message = error instanceof Error ? error.message : typeof error === 'string' ? error : t('nodePacks.downloadFailed')
      setDownloading((prev) => prev[downloadId] ? ({ ...prev, [downloadId]: { ...prev[downloadId], paused: false } }) : prev)
      setDownloadErrors((prev) => ({ ...prev, [downloadId]: message }))
    }
  }

  async function handleCancelDownload(downloadId: string) {
    setDownloading((prev) => { const next = { ...prev }; delete next[downloadId]; return next })
    try {
      const result = await window.polykit.model.cancelDownload(downloadId)
      if (!result.cancelled) {
        setDownloadErrors((prev) => ({ ...prev, [downloadId]: t('nodePacks.downloadFailed') }))
      }
    } catch (error: unknown) {
      const message = error instanceof Error ? error.message : typeof error === 'string' ? error : t('nodePacks.downloadFailed')
      setDownloadErrors((prev) => ({ ...prev, [downloadId]: message }))
    }
  }

  async function handleUninstallNode(downloadId: string) {
    await window.polykit.model.delete(downloadId)
    refreshInstalledIds(useNodePacksStore.getState().modelNodePacks)
  }

  async function handleGHInstall() {
    const url = ghUrl.trim()
    if (!url) { setGhErr(t('nodePacks.githubUrlRequired')); return }
    if (!url.includes('github.com')) { setGhErr(t('nodePacks.githubUrlInvalid')); return }
    setGhErr(null)
    clearInstall()
    const result = await installFromGH(url)
    if (result.success) {
      setShowGHForm(false)
      setGhUrl('')
    } else {
      setGhErr(result.error ?? t('nodePacks.installationFailed'))
    }
  }

  async function handleLocalInstall() {
    setGhErr(null)
    clearInstall()
    const result = await installFromLocal()
    if ('cancelled' in result && result.cancelled) return
    if (!result.success) setGhErr(result.error ?? t('nodePacks.installationFailed'))
  }

  function openUninstallModal(extId: string) {
    const ext = allNodePacks.find((item) => item.id === extId)
    if (ext?.type === 'model') {
      const installedModels = ext.nodes.filter((node) => installedVariantIds.includes(`${extId}/${node.id}`))
      setModelsToDelete(new Set(installedModels.map((node) => getNodeDownloadId(ext, node))))
    } else {
      setModelsToDelete(new Set())
    }
    setUninstallTarget(extId)
  }

  function closeUninstallModal() {
    setUninstallTarget(null)
    setModelsToDelete(new Set())
    setUninstallError(null)
  }

  async function handleUninstallNodePack(nodePackId: string) {
    for (const modelId of modelsToDelete) {
      await window.polykit.model.delete(modelId)
    }
    const result = await uninstallExt(nodePackId)
    if (!result.success) {
      setUninstallError(result.error ?? t('nodePacks.deleteFolderFailed'))
      return
    }
    closeUninstallModal()
    setSelectedId((id) => (id === nodePackId ? null : id))
    refreshInstalledIds(useNodePacksStore.getState().modelNodePacks)
  }

  const isInstalling = installProgress !== null && installProgress.step !== 'done' && installProgress.step !== 'error'
  const isBusy = isInstalling || Object.keys(downloading).length > 0

  const counts = useMemo(() => ({
    all: allNodePacks.length,
    process: allNodePacks.filter((item) => item.type === 'process').length,
    model: allNodePacks.filter((item) => item.type === 'model').length,
    official: allNodePacks.filter((item) => item.trusted).length,
  }), [allNodePacks])

  const filteredNodePacks = useMemo(() => {
    const query = search.trim().toLowerCase()
    const list = allNodePacks.filter((item) => {
      if (query && !nodePackSearchText(item, language).includes(query)) return false
      if (filter === 'process') return item.type === 'process'
      if (filter === 'model') return item.type === 'model'
      if (filter === 'official') return item.trusted
      return true
    })
    return [...list].sort((a, b) => localizedNodePackName(a, language).localeCompare(localizedNodePackName(b, language), language))
  }, [allNodePacks, search, filter, language])

  const processList = filteredNodePacks.filter((item) => item.type === 'process')
  const modelList = filteredNodePacks.filter((item) => item.type === 'model')
  const grouped = filter === 'all' || filter === 'official'
  // Keep only filters that actually narrow the current catalog. The active
  // filter remains visible while it is selected so a data refresh cannot
  // strand the user on a hidden tab.
  const visibleFilters = FILTERS.filter((id) => (
    id === 'all' || id === filter || (counts[id] > 0 && counts[id] < counts.all)
  ))
  const showGroupHeadings = processList.length > 0 && modelList.length > 0
  const selectedExt = selectedId ? allNodePacks.find((item) => item.id === selectedId) ?? null : null

  const uninstallExtTarget = uninstallTarget ? allNodePacks.find((item) => item.id === uninstallTarget) ?? null : null
  const uninstallDisplayName = uninstallExtTarget
    ? localizedNodePackName(uninstallExtTarget, language)
    : uninstallTarget ?? t('nodePacks.nodePackFallback')
  const installedModelsForUninstall = uninstallExtTarget?.type === 'model'
    ? uninstallExtTarget.nodes.filter((node) => installedVariantIds.includes(`${uninstallExtTarget.id}/${node.id}`))
    : []

  function extLoadError(ext: AnyNodePack): string | undefined {
    return loadErrors[ext.id] ?? ext.nodes.map((node) => loadErrors[`${ext.id}/${node.id}`]).find(Boolean)
  }

  function extDownloadError(ext: AnyNodePack): string | undefined {
    return ext.nodes.map((node) => downloadErrors[getNodeDownloadId(ext, node)]).find(Boolean)
  }

  function installProgressLabel(): string {
    if (!installProgress) return ''
    switch (installProgress.step) {
      case 'downloading': return t('nodePacks.progressDownloading', { percent: installProgress.percent ?? 0 })
      case 'extracting': return t('nodePacks.progressExtracting')
      case 'validating': return t('nodePacks.progressValidating')
      case 'setting_up': return t('nodePacks.progressSettingUp')
      case 'done': return t('nodePacks.progressInstalled')
      default: return ''
    }
  }

  function filterLabel(id: FilterId): string {
    if (id === 'all') return t('nodePacks.all')
    if (id === 'process') return t('nodePacks.processors')
    if (id === 'model') return t('nodePacks.models')
    return t('nodePacks.official')
  }

  const cardHandlers = {
    installedIds: installedVariantIds,
    downloading,
    disabled: isBusy,
    onInstall: handleInstallNode,
    onInstallAll: handleInstallAll,
    onPauseDownload: handlePauseDownload,
    onCancelDownload: handleCancelDownload,
    onOpen: (ext: AnyNodePack) => setSelectedId(ext.id),
  }

  if (!initialLoadReady) {
    return (
      <div className="relative flex h-full flex-col overflow-hidden bg-background">
        <div className="flex min-h-0 flex-1 items-center justify-center">
          <div className="flex items-center gap-2.5 rounded-lg border border-border bg-card px-3 py-2 text-xs text-muted-foreground shadow-sm" role="status" aria-live="polite">
            <span className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-muted border-t-primary" />
            {t('common.loading')}
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="relative flex h-full flex-col overflow-hidden">
      <div className="flex shrink-0 flex-wrap items-center gap-3 border-b border-border/45 bg-card/65 px-5 py-3">
        <div className="mr-auto min-w-[140px]">
          <h1 className="text-base font-semibold tracking-tight text-foreground">{t('nodePacks.title')}</h1>
        </div>

        <div className="relative min-w-[220px] flex-[1_1_260px] max-w-md">
          <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            ref={searchRef}
            type="text"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder={t('nodePacks.search')}
            className="h-9 pl-9 pr-10"
          />
          {search ? (
            <Button
              type="button"
              variant="ghost"
              size="icon"
              className="absolute right-1 top-1/2 h-7 w-7 -translate-y-1/2 text-muted-foreground"
              onClick={() => setSearch('')}
              aria-label={t('nodePacks.clearSearch')}
            >
              <X className="h-3.5 w-3.5" />
            </Button>
          ) : (
            <kbd className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 rounded border border-border bg-muted px-1.5 py-0.5 font-mono text-[10px] text-muted-foreground">/</kbd>
          )}
        </div>

        <div className="flex shrink-0 gap-2">
          {!isWeb && (
            <Button
              type="button"
              variant="outline"
              size="sm"
              className="gap-2"
              onClick={() => {
                setShowGHForm((value) => !value)
                setGhErr(null)
                clearInstall()
              }}
            >
              <Github className="h-4 w-4" />
              {showGHForm ? t('common.cancel') : t('nodePacks.installGithub')}
            </Button>
          )}
          {!isWeb && (
            <Button
              type="button"
              variant="outline"
              size="sm"
              className="gap-2"
              onClick={handleLocalInstall}
              disabled={isInstalling}
              title={t('nodePacks.linkLocalHint')}
            >
              <FolderOpen className="h-4 w-4" />
              {t('nodePacks.linkLocal')}
            </Button>
          )}
        </div>
      </div>

      {visibleFilters.length > 1 && (
        <div className="flex shrink-0 items-center gap-1 border-b border-border/45 bg-background px-5 py-2">
          {visibleFilters.map((id) => (
            <Button
              key={id}
              type="button"
              variant={filter === id ? 'secondary' : 'ghost'}
              size="sm"
              className="h-8 gap-1.5 px-2.5"
              onClick={() => setFilter(id)}
            >
              {filterLabel(id)}
              <Badge variant="outline" className="h-5 min-w-5 justify-center px-1.5 font-mono text-[10px] text-muted-foreground">
                {counts[id]}
              </Badge>
            </Button>
          ))}
        </div>
      )}

      {showGHForm && !isWeb && (
        <div className="shrink-0 animate-fade-in px-5 pb-4">
          <Card className="flex flex-col gap-3 p-4 shadow-none">
            <div className="flex gap-2">
              <Input
                type="text"
                value={ghUrl}
                onChange={(event) => { setGhUrl(event.target.value); setGhErr(null); clearInstall() }}
                onKeyDown={(event) => event.key === 'Enter' && !isInstalling && handleGHInstall()}
                placeholder="https://github.com/owner/repo"
                autoFocus
                disabled={isInstalling}
                className="flex-1"
              />
              <Button
                type="button"
                onClick={handleGHInstall}
                disabled={!ghUrl.trim() || isInstalling}
                className="gap-1.5"
              >
                {isInstalling ? <LoaderCircle className="h-4 w-4 animate-spin" /> : <Download className="h-4 w-4" />}
                {isInstalling ? installProgressLabel() : t('nodePacks.install')}
              </Button>
            </div>

            {isInstalling && installProgress?.step === 'downloading' && (
              <div className="h-1 overflow-hidden rounded-full bg-muted" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={installProgress.percent ?? 0}>
                <div className="h-full rounded-full bg-primary transition-all duration-300" style={{ width: `${installProgress.percent ?? 0}%` }} />
              </div>
            )}

            {isInstalling && installProgress?.step === 'setting_up' && (
              <div className="flex flex-col gap-2 rounded-md border border-border bg-muted/30 px-3 py-2.5">
                <div className="flex items-center justify-between gap-3">
                  <div className="flex min-w-0 items-center gap-2">
                    <LoaderCircle className="h-3.5 w-3.5 shrink-0 animate-spin text-primary" />
                    <span className="truncate text-[11px] text-muted-foreground">
                      {installProgress.message ?? t('nodePacks.progressSettingUp')}
                    </span>
                  </div>
                  <span className="shrink-0 text-[10px] text-muted-foreground">{t('nodePacks.mayTakeMinutes')}</span>
                </div>
                <div className="h-0.5 overflow-hidden rounded-full bg-muted">
                  <div className="h-full w-1/3 animate-[slide_1.5s_ease-in-out_infinite] rounded-full bg-primary" />
                </div>
              </div>
            )}

            {installProgress?.step === 'done' && (
              <div className="flex items-center gap-2 rounded-md border border-emerald-500/25 bg-emerald-500/10 px-3 py-2">
                <CheckCircle2 className="h-3.5 w-3.5 shrink-0 text-emerald-400" />
                <p className="text-[11px] text-emerald-400">{t('nodePacks.installSuccess')}</p>
              </div>
            )}

            {ghErr && (
              <div className="flex items-center gap-2 rounded-md border border-destructive/25 bg-destructive/10 px-3 py-2">
                <CircleAlert className="h-3.5 w-3.5 shrink-0 text-destructive" />
                <p className="text-[11px] text-destructive">{ghErr}</p>
              </div>
            )}

            <p className="text-[10px] text-muted-foreground">{t('nodePacks.repoRequirement')}</p>
          </Card>
        </div>
      )}

      <div className="min-h-0 flex-1 overflow-y-auto px-5 pb-8">
        {allNodePacks.length === 0 && extLoading ? (
          <div className="flex items-center justify-center py-16">
            <LoaderCircle className="h-5 w-5 animate-spin text-muted-foreground" />
          </div>
        ) : allNodePacks.length === 0 ? (
          <Card className="flex flex-col items-center justify-center gap-3 border-dashed bg-card/40 py-16 shadow-none">
            <Box className="h-8 w-8 text-muted-foreground/60" strokeWidth={1.5} />
            <div className="text-center">
              <p className="text-sm font-medium text-muted-foreground">{t('nodePacks.noInstalled')}</p>
              <p className="mt-1 text-xs text-muted-foreground">{t('nodePacks.emptyHint')}</p>
            </div>
          </Card>
        ) : filteredNodePacks.length === 0 ? (
          <div className="flex flex-col items-center justify-center gap-2 py-16">
            <Search className="h-7 w-7 text-muted-foreground/60" strokeWidth={1.5} />
            <p className="text-sm text-muted-foreground">{t('nodePacks.noMatch', { query: search })}</p>
          </div>
        ) : grouped ? (
          <>
            {processList.length > 0 && (
              <section className="mt-1">
                {showGroupHeadings && <div className="mb-4 flex items-center gap-3 px-0.5">
                  <span className="grid h-6 w-6 place-items-center rounded-md border border-sky-500/25 bg-sky-500/10 p-1 text-sky-400">
                    <Box className="h-full w-full" strokeWidth={1.5} />
                  </span>
                  <h2 className="text-[13px] font-semibold tracking-wide text-foreground">{t('nodePacks.processors')}</h2>
                  <Badge variant="outline" className="font-mono text-[10px] text-muted-foreground">{processList.length}</Badge>
                  <span className="h-px flex-1 bg-border/45" />
                </div>}
                <div className="grid gap-3" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))' }}>
                  {processList.map((ext) => <NodePackCard key={ext.id} ext={ext} loadError={extLoadError(ext)} {...cardHandlers} />)}
                </div>
              </section>
            )}
            {modelList.length > 0 && (
              <section className={showGroupHeadings ? 'mt-10' : 'mt-1'}>
                {showGroupHeadings && <div className="mb-4 flex items-center gap-3 px-0.5">
                  <span className="grid h-6 w-6 place-items-center rounded-md border border-primary/25 bg-primary/10 p-1 text-primary">
                    <Sparkles className="h-full w-full" strokeWidth={1.5} />
                  </span>
                  <h2 className="text-[13px] font-semibold tracking-wide text-foreground">{t('nodePacks.models')}</h2>
                  <Badge variant="outline" className="font-mono text-[10px] text-muted-foreground">{modelList.length}</Badge>
                  <span className="h-px flex-1 bg-border/45" />
                </div>}
                <div className="grid gap-3" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))' }}>
                  {modelList.map((ext) => <NodePackCard key={ext.id} ext={ext} loadError={extLoadError(ext)} {...cardHandlers} />)}
                </div>
              </section>
            )}
          </>
        ) : (
          <div className="mt-1 grid gap-4" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))' }}>
            {filteredNodePacks.map((ext) => <NodePackCard key={ext.id} ext={ext} loadError={extLoadError(ext)} {...cardHandlers} />)}
          </div>
        )}
      </div>

      {selectedExt && (
        <NodePackDrawer
          ext={selectedExt}
          installedIds={installedVariantIds}
          downloading={downloading}
          downloadError={extDownloadError(selectedExt)}
          loadError={extLoadError(selectedExt)}
          disabled={isBusy}
          onInstall={handleInstallNode}
          onInstallAll={handleInstallAll}
          onPauseDownload={handlePauseDownload}
          onCancelDownload={handleCancelDownload}
          onUninstallNode={handleUninstallNode}
          onUninstall={openUninstallModal}
          onRepaired={() => reloadNodePacks()}
          onSynced={() => reloadNodePacks()}
          webMode={isWeb}
          onClose={() => setSelectedId(null)}
        />
      )}

      <Dialog open={!!uninstallTarget} onOpenChange={(open) => { if (!open) closeUninstallModal() }}>
        <DialogContent className="max-w-md">
          <DialogHeader className="pr-8">
            <div className="mb-1 flex h-10 w-10 items-center justify-center rounded-lg border border-destructive/20 bg-destructive/10 text-destructive">
              <Trash2 className="h-4 w-4" />
            </div>
            <DialogTitle>{t('nodePacks.uninstallTitle', { name: uninstallDisplayName })}</DialogTitle>
            <DialogDescription>{t('nodePacks.uninstallDescription')}</DialogDescription>
          </DialogHeader>

          {installedModelsForUninstall.length > 0 && uninstallTarget && (
            <div className="flex flex-col gap-2">
              <p className="text-xs font-medium text-muted-foreground">{t('nodePacks.alsoDeleteWeights')}</p>
              {installedModelsForUninstall.map((variant) => {
                const id = `${uninstallTarget}/${variant.id}`
                const checked = modelsToDelete.has(id)
                return (
                  <label key={variant.id} className="flex cursor-pointer items-center gap-2.5 rounded-md border border-border bg-muted/25 px-3 py-2 hover:bg-muted/40">
                    <input
                      type="checkbox"
                      checked={checked}
                      onChange={() => {
                        setModelsToDelete((previous) => {
                          const next = new Set(previous)
                          if (checked) next.delete(id)
                          else next.add(id)
                          return next
                        })
                      }}
                      className="h-4 w-4 accent-primary"
                    />
                    <span className="text-xs text-foreground">{formatModelName(id)}</span>
                  </label>
                )
              })}
            </div>
          )}

          {uninstallError && (
            <div className="flex items-start gap-2 rounded-md border border-destructive/25 bg-destructive/10 px-3 py-2.5">
              <CircleAlert className="mt-0.5 h-3.5 w-3.5 shrink-0 text-destructive" />
              <p className="break-words text-[11px] text-destructive">{uninstallError}</p>
            </div>
          )}

          <DialogFooter>
            <Button type="button" variant="outline" onClick={closeUninstallModal}>{t('common.cancel')}</Button>
            <Button
              type="button"
              variant="destructive"
              onClick={() => uninstallTarget && handleUninstallNodePack(uninstallTarget)}
            >
              {t('nodePacks.uninstall')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}

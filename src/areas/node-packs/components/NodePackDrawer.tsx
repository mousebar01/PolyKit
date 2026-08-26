import { useState } from 'react'
import {
  ChevronRight,
  CircleAlert,
  Download,
  ExternalLink,
  LoaderCircle,
  RefreshCw,
  RotateCcw,
  Trash2,
  TriangleAlert,
  Unlink,
} from 'lucide-react'

import { Badge, Button, Card, Dialog, DialogContent, DialogDescription, DialogTitle } from '@shared/components/ui'
import { useNavStore } from '@shared/stores/navStore'
import type { AnyNodePack, NodePackNode } from '@shared/types/runtime.d'
import { localizedNodeName, localizedNodePackDescription, localizedNodePackName } from '../nodePackI18n'
import { useNodePacksI18n } from '../i18n'
import {
  DownloadMap,
  ICONS,
  IOBadge,
  NodeInstallControl,
  TypePill,
  extInstallSummary,
  formatBytes,
  getNodeDownloadId,
  getNodeState,
} from './nodePackShared'

interface Props {
  ext: AnyNodePack
  installedIds: string[]
  downloading: DownloadMap
  downloadError?: string
  loadError?: string
  disabled?: boolean
  webMode?: boolean
  onInstall: (node: NodePackNode, fullId: string, downloadId: string) => void
  onInstallAll: (ext: AnyNodePack) => void
  onPauseDownload: (downloadId: string) => void
  onCancelDownload: (downloadId: string) => void
  onUninstallNode: (downloadId: string) => void
  onUninstall: (extId: string) => void
  onRepaired: () => void
  onSynced: () => void
  onClose: () => void
}

export function NodePackDrawer({
  ext, installedIds, downloading, downloadError, loadError, disabled, webMode = false,
  onInstall, onInstallAll, onPauseDownload, onCancelDownload,
  onUninstallNode, onUninstall, onRepaired, onSynced, onClose,
}: Props): JSX.Element {
  const { language, t } = useNodePacksI18n()
  const navigate = useNavStore((s) => s.navigate)
  const openNodePackInWorkflow = useNavStore((s) => s.openNodePackInWorkflow)
  const [repairing, setRepairing] = useState(false)
  const [repairError, setRepairError] = useState<string | null>(null)
  const [syncing, setSyncing] = useState(false)
  const [syncError, setSyncError] = useState<string | null>(null)

  const isModel = ext.type === 'model'
  const isCorrupted = !!ext.corrupted
  const displayName = localizedNodePackName(ext, language)
  const description = localizedNodePackDescription(ext, language)
  const corruptedMsg =
    ext.manifestError === 'invalid'
      ? t('nodePacks.corruptedInvalid')
      : ext.manifestError === 'incomplete'
        ? t('nodePacks.corruptedIncomplete')
        : t('nodePacks.corruptedMissing')
  const isLocal = typeof ext.source === 'string' && ext.source.startsWith('local://')
  const localPath = isLocal ? ext.source!.replace('local://', '') : null
  const sourceLabel = localPath ?? ext.source ?? (ext.builtin ? t('nodePacks.builtIn') : '—')
  const sourceUrl = typeof ext.source === 'string' && /^https?:\/\//i.test(ext.source) ? ext.source : null
  const { total, done, installing, hasAvailable } = extInstallSummary(ext, installedIds, downloading)

  async function handleRepair() {
    setRepairing(true)
    setRepairError(null)
    const result = await window.polykit.nodePacks.repair(ext.id)
    setRepairing(false)
    if (result.success) onRepaired()
    else setRepairError(result.error ?? t('nodePacks.repairFailed'))
  }

  async function handleSync() {
    setSyncing(true)
    setSyncError(null)
    try {
      const result = await window.polykit.nodePacks.reload()
      if (!result.success) setSyncError(result.error ?? t('nodePacks.syncFailed'))
      else onSynced()
    } catch (error) {
      setSyncError(String(error))
    } finally {
      setSyncing(false)
    }
  }

  const error = downloadError ?? syncError ?? repairError ?? loadError

  return (
    <Dialog open onOpenChange={(open) => { if (!open) onClose() }}>
      <DialogContent className="bottom-0 left-auto right-0 top-0 flex h-full w-[418px] max-w-[92vw] translate-x-0 translate-y-0 flex-col gap-0 rounded-none border-y-0 border-r-0 p-0 shadow-2xl">
        <div className="shrink-0 border-b border-border px-5 pb-4 pt-5">
          <div className="flex items-center gap-3.5 pr-8">
            <div className={`h-[52px] w-[52px] shrink-0 rounded-xl border border-border bg-muted p-3 ${isModel ? 'text-primary' : 'text-emerald-400'}`}>
              {isModel ? ICONS.spark : ICONS.cube}
            </div>
            <div className="min-w-0">
              <DialogTitle className="truncate text-lg">{displayName}</DialogTitle>
              <DialogDescription className="sr-only">{description?.trim() || t('nodePacks.openDetails', { name: displayName })}</DialogDescription>
              <div className="mt-1.5 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                <TypePill type={ext.type} />
                {isLocal && <Badge variant="outline" className="border-orange-500/25 text-orange-400">{t('nodePacks.local')}</Badge>}
              </div>
            </div>
          </div>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto px-5 py-5">
          {isCorrupted && (
            <div className="mb-5 flex items-start gap-2.5 rounded-md border border-amber-500/30 bg-amber-500/10 px-3 py-3">
              <TriangleAlert className="mt-0.5 h-3.5 w-3.5 shrink-0 text-amber-400" />
              <div className="min-w-0">
                <p className="text-xs font-semibold text-amber-400">{t('nodePacks.corruptedInstallation')}</p>
                <p className="mt-1 text-[11px] leading-5 text-amber-400/80">
                  {ext.builtin ? t('nodePacks.corruptedBuiltin') : corruptedMsg}
                </p>
              </div>
            </div>
          )}

          {error && (
            <div className="mb-5 flex items-start gap-2 rounded-md border border-destructive/25 bg-destructive/10 px-3 py-2.5">
              <CircleAlert className="mt-0.5 h-3.5 w-3.5 shrink-0 text-destructive" />
              <p className="break-all text-[11px] text-destructive">{error}</p>
            </div>
          )}

          <section className="mb-6">
            <h4 className="mb-2.5 text-[10.5px] font-semibold uppercase tracking-[0.07em] text-muted-foreground">{t('nodePacks.description')}</h4>
            <p className="text-[13px] leading-6 text-muted-foreground">{description?.trim() || t('nodePacks.noDescription')}</p>
          </section>

          <section className="mb-6">
            <h4 className="mb-2.5 text-[10.5px] font-semibold uppercase tracking-[0.07em] text-muted-foreground">
              {isModel ? t('nodePacks.nodesInstalled', { done, total }) : t('nodePacks.actionsCount', { total })}
            </h4>
            <div className="flex flex-col gap-2">
              {ext.nodes.map((node, index) => {
                const fullId = `${ext.id}/${node.id}`
                const downloadId = getNodeDownloadId(ext, node)
                const isFirstSharedNode = ext.nodes.findIndex((candidate) => getNodeDownloadId(ext, candidate) === downloadId) === index
                const state = getNodeState(ext.id, node, installedIds, downloading, downloadId)
                const dl = state.kind === 'downloading' ? state.dl : null
                const sub =
                  state.kind === 'ready' ? t('nodePacks.availableOnGraph')
                    : state.kind === 'installed' ? t('nodePacks.installed')
                      : state.kind === 'available' ? t('nodePacks.notInstalled')
                        : dl?.paused ? t('nodePacks.downloadPaused')
                          : t('nodePacks.progressDownloading', { percent: dl?.percent ?? 0 })

                return (
                  <Card key={node.id} className="rounded-lg bg-muted/20 p-3.5 shadow-none">
                    <div className="flex items-center justify-between gap-3">
                      <div className="min-w-0">
                        <div className="truncate text-[13px] font-semibold text-foreground">{localizedNodeName(node, language)}</div>
                        <div className="mt-0.5 truncate text-[11px] text-muted-foreground">{sub}</div>
                      </div>
                      <div className="flex shrink-0 flex-col items-end gap-2">
                        <IOBadge node={node} />
                        {isModel && (
                          <div className="flex items-center gap-1.5">
                            <NodeInstallControl
                              state={state}
                              disabled={disabled}
                              onInstall={() => onInstall(node, fullId, downloadId)}
                              onPause={() => onPauseDownload(downloadId)}
                              onResume={() => onInstall(node, fullId, downloadId)}
                              onCancel={() => onCancelDownload(downloadId)}
                            />
                            {state.kind === 'installed' && isFirstSharedNode && (
                              <Button
                                type="button"
                                variant="ghost"
                                size="icon"
                                className="h-7 w-7 text-muted-foreground hover:bg-destructive/10 hover:text-destructive"
                                onClick={() => onUninstallNode(downloadId)}
                                disabled={disabled}
                                title={t('nodePacks.removeModelWeights')}
                                aria-label={t('nodePacks.removeModelWeights')}
                              >
                                <Trash2 className="h-3.5 w-3.5" />
                              </Button>
                            )}
                          </div>
                        )}
                      </div>
                    </div>

                    {dl && (
                      <div className="mt-3 flex flex-col gap-1">
                        <div className="flex items-center justify-between gap-2 text-[10px] text-muted-foreground">
                          <span className="truncate">{dl.paused ? t('nodePacks.paused') : (dl.file ?? dl.status ?? t('nodePacks.progressDownloading', { percent: dl.percent }))}</span>
                          <span className="shrink-0 font-mono">
                            {dl.fileIndex && dl.totalFiles ? `${dl.fileIndex}/${dl.totalFiles} · ${dl.percent}%` : `${dl.percent}%`}
                          </span>
                        </div>
                        <div className="flex items-center justify-between gap-2 text-[10px]">
                          <span className="truncate text-muted-foreground">
                            {dl.totalBytes && dl.totalBytes > 0
                              ? `${formatBytes(dl.bytesDownloaded)} / ${formatBytes(dl.totalBytes)}`
                              : formatBytes(dl.bytesDownloaded)}
                          </span>
                          {(dl.stalledSeconds ?? 0) >= 30 && <span className="shrink-0 text-amber-400">{t('nodePacks.noProgress', { seconds: dl.stalledSeconds ?? 0 })}</span>}
                        </div>
                      </div>
                    )}
                  </Card>
                )
              })}
            </div>
          </section>

          <section>
            <h4 className="mb-2.5 text-[10.5px] font-semibold uppercase tracking-[0.07em] text-muted-foreground">{t('nodePacks.details')}</h4>
            <dl className="grid grid-cols-[1fr_auto] gap-x-3.5 gap-y-2.5 text-xs">
              <dt className="text-muted-foreground">{t('nodePacks.source')}</dt>
              <dd className="flex min-w-0 max-w-[230px] items-center justify-end text-right font-mono text-[11.5px] text-foreground/75" style={{ direction: 'rtl' }} title={sourceLabel}>
                {sourceUrl ? (
                  <a
                    href={sourceUrl}
                    target="_blank"
                    rel="noreferrer noopener"
                    className="inline-flex min-w-0 max-w-full items-center gap-1 rounded-sm text-primary/80 underline decoration-primary/30 underline-offset-2 transition-colors hover:text-primary hover:decoration-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                    aria-label={sourceLabel}
                  >
                    <span className="truncate">{sourceLabel}</span>
                    <ExternalLink className="h-3 w-3 shrink-0" aria-hidden="true" />
                  </a>
                ) : sourceLabel}
              </dd>
              <dt className="text-muted-foreground">{t('nodePacks.nodes')}</dt>
              <dd className="text-right font-mono text-[11.5px] text-foreground/75">{ext.nodes.length}</dd>
              {ext.type === 'model' && (
                <>
                  <dt className="text-muted-foreground">{t('nodePacks.environment')}</dt>
                  <dd className="text-right font-mono text-[11.5px] text-foreground/75">{ext.env === 'isolated' ? t('nodePacks.isolatedVenv') : t('nodePacks.shared')}</dd>
                  <dt className="text-muted-foreground">{t('nodePacks.dependencies')}</dt>
                  <dd className="text-right font-mono text-[11.5px] text-foreground/75">{ext.requirements ? t('nodePacks.packages', { count: ext.requirements.length }) : '—'}</dd>
                  {ext.download && (
                    <>
                      <dt className="text-muted-foreground">{t('nodePacks.download')}</dt>
                      <dd className="max-w-[230px] truncate text-right text-[11.5px] text-foreground/75" title={ext.download.note ?? ext.download.repo ?? ''}>
                        {ext.download.repo ?? ext.download.kind ?? '—'}
                      </dd>
                    </>
                  )}
                </>
              )}
            </dl>
          </section>
        </div>

        <div className="flex shrink-0 items-center gap-2.5 border-t border-border px-5 py-4">
          {isCorrupted && !ext.builtin && !webMode ? (
            <Button type="button" variant="destructive" className="flex-1 gap-2" onClick={() => onUninstall(ext.id)} disabled={disabled}>
              <Trash2 className="h-4 w-4" />
              {t('nodePacks.deleteBrokenFolder')}
            </Button>
          ) : isModel && hasAvailable ? (
            <Button type="button" className="flex-1 gap-2" onClick={() => onInstallAll(ext)} disabled={disabled || installing}>
              {installing ? <LoaderCircle className="h-4 w-4 animate-spin" /> : <Download className="h-4 w-4" />}
              {installing ? t('nodePacks.installing') : t('nodePacks.installAllNodes')}
            </Button>
          ) : (
            <Button
              type="button"
              className="flex-1 gap-2"
              onClick={() => {
                onClose()
                const firstNode = ext.nodes[0]
                if (firstNode) openNodePackInWorkflow(`${ext.id}/${firstNode.id}`)
                else navigate('workflows')
              }}
            >
              {t('nodePacks.useInWorkflow')}
              <ChevronRight className="h-4 w-4" />
            </Button>
          )}

          {isModel && !isCorrupted && (
            <Button
              type="button"
              variant="outline"
              className="gap-1.5"
              onClick={handleRepair}
              disabled={repairing || disabled}
              title={t('nodePacks.repairHint')}
            >
              {repairing ? <LoaderCircle className="h-3.5 w-3.5 animate-spin" /> : <RotateCcw className="h-3.5 w-3.5" />}
              {repairing ? t('nodePacks.repairing') : t('nodePacks.repair')}
            </Button>
          )}

          {isLocal && !webMode && (
            <Button
              type="button"
              variant="outline"
              size="icon"
              onClick={handleSync}
              disabled={syncing || disabled}
              title={t('nodePacks.syncHint')}
              aria-label={t('nodePacks.syncLocal')}
            >
              <RefreshCw className={`h-4 w-4 ${syncing ? 'animate-spin' : ''}`} />
            </Button>
          )}

          {!ext.builtin && !isCorrupted && !webMode && (
            <Button
              type="button"
              variant="outline"
              size="icon"
              className="text-muted-foreground hover:border-destructive/40 hover:bg-destructive/10 hover:text-destructive"
              onClick={() => onUninstall(ext.id)}
              disabled={disabled}
              title={isLocal ? t('nodePacks.unlinkLocal') : t('nodePacks.uninstallNodePack')}
              aria-label={isLocal ? t('nodePacks.unlinkLocal') : t('nodePacks.uninstallNodePack')}
            >
              {isLocal ? <Unlink className="h-4 w-4" /> : <Trash2 className="h-4 w-4" />}
            </Button>
          )}
        </div>
      </DialogContent>
    </Dialog>
  )
}

import type { AnyNodePack, NodePackNode } from '@shared/types/runtime.d'
export type { AnyNodePack }
export type { NodePackNode } from '@shared/types/runtime.d'

import { Button, Card } from '@shared/components/ui'
import { localizedNodeName, localizedNodePackDescription, localizedNodePackName } from '../nodePackI18n'
import { useNodePacksI18n } from '../i18n'
import {
  DownloadMap,
  ICONS,
  IOBadge,
  NodeInstallControl,
  StatusBadge,
  extInstallSummary,
  getNodeDownloadId,
  getNodeState,
} from './nodePackShared'

interface Props {
  ext: AnyNodePack
  installedIds: string[]
  downloading: DownloadMap
  loadError?: string
  disabled?: boolean
  onInstall: (node: NodePackNode, fullId: string, downloadId: string) => void
  onInstallAll: (ext: AnyNodePack) => void
  onPauseDownload: (downloadId: string) => void
  onCancelDownload: (downloadId: string) => void
  onOpen: (ext: AnyNodePack) => void
}

export function NodePackCard({
  ext, installedIds, downloading, loadError, disabled,
  onInstall, onInstallAll, onPauseDownload, onCancelDownload, onOpen,
}: Props): JSX.Element {
  const { language, t } = useNodePacksI18n()
  const isModel = ext.type === 'model'
  const isLocal = typeof ext.source === 'string' && ext.source.startsWith('local://')
  const { total, done, installing, hasAvailable } = extInstallSummary(ext, installedIds, downloading)
  const displayName = localizedNodePackName(ext, language)
  const description = localizedNodePackDescription(ext, language)

  let status: JSX.Element | null
  if (loadError) {
    status = <StatusBadge tone="amber">{t('nodePacks.loadError')}</StatusBadge>
  } else if (installing) {
    status = <StatusBadge tone="brand">{t('nodePacks.installing')}</StatusBadge>
  } else if (!isModel) {
    status = <StatusBadge tone="green">{t('nodePacks.ready')}</StatusBadge>
  } else if (total === 0 || done === total) {
    // Each node already shows its installed state; repeating an aggregate
    // "all ready" label adds noise to an otherwise complete card.
    status = null
  } else {
    status = <StatusBadge tone="amber">{t('nodePacks.nodesInstalledStatus', { done, total })}</StatusBadge>
  }

  return (
    <Card className="group relative flex min-h-[210px] flex-col overflow-hidden rounded-lg bg-card p-4 shadow-none transition-colors duration-150 hover:border-primary/35 hover:bg-muted/30">
      <button
        type="button"
        className="absolute inset-0 z-10 cursor-pointer rounded-lg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"
        onClick={() => onOpen(ext)}
        aria-label={t('nodePacks.openDetails', { name: displayName })}
      />

      <div className="flex items-center gap-3">
        <div className={`h-10 w-10 shrink-0 rounded-md bg-muted p-2.5 ${isModel ? 'text-primary' : 'text-sky-400'}`}>
          {isModel ? ICONS.spark : ICONS.cube}
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="truncate text-sm font-semibold text-foreground">{displayName}</span>
          </div>
          {isLocal && (
            <div className="mt-1 flex flex-wrap items-center gap-1.5 text-[11px] text-muted-foreground">
              <span className="text-sky-400/90">{t('nodePacks.local')}</span>
            </div>
          )}
        </div>
      </div>

      <p className="mt-3 min-h-[2.5rem] line-clamp-2 text-xs leading-5 text-muted-foreground">
        {description?.trim() || '—'}
      </p>

      {loadError && (
        <div className="mt-2 rounded-md border border-destructive/25 bg-destructive/10 px-2.5 py-1.5">
          <p className="line-clamp-1 break-all text-[10px] text-destructive">{loadError}</p>
        </div>
      )}

      {ext.nodes.length > 0 && (
        <div className="mt-3 flex flex-col gap-1.5">
          {ext.nodes.map((node) => {
            const fullId = `${ext.id}/${node.id}`
            const downloadId = getNodeDownloadId(ext, node)
            const state = getNodeState(ext.id, node, installedIds, downloading, downloadId)
            return (
              <div
                key={node.id}
                className="flex items-center justify-between gap-2.5 rounded-md bg-muted/45 px-2.5 py-1.5"
              >
                <div className="flex min-w-0 flex-col items-start gap-1">
                  <span className="max-w-full truncate text-xs font-medium text-foreground">{localizedNodeName(node, language)}</span>
                  <IOBadge node={node} />
                </div>
                {isModel && (
                  <div className="relative z-20 shrink-0">
                    <NodeInstallControl
                      state={state}
                      disabled={disabled}
                      onInstall={() => onInstall(node, fullId, downloadId)}
                      onPause={() => onPauseDownload(downloadId)}
                      onResume={() => onInstall(node, fullId, downloadId)}
                      onCancel={() => onCancelDownload(downloadId)}
                    />
                  </div>
                )}
              </div>
            )
          })}
        </div>
      )}

      {(status || (isModel && hasAvailable && !installing)) && (
        <div className="mt-auto flex items-center justify-between gap-2 pt-3">
          {status}
          {isModel && hasAvailable && !installing && (
            <Button
              type="button"
              size="sm"
              className="relative z-20 h-8 gap-1.5"
              onClick={() => onInstallAll(ext)}
              disabled={disabled}
            >
              <span className="h-3 w-3">{ICONS.download}</span>
              {t('nodePacks.installAll')}
            </Button>
          )}
        </div>
      )}
    </Card>
  )
}

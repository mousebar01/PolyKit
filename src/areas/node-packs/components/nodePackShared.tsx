import type { AnyNodePack, NodePackNode } from '@shared/types/runtime.d'
import { ArrowRight, Box, Check, Download, Pause, Play, ShieldCheck, Sparkles, X } from 'lucide-react'

import { Badge, Button } from '@shared/components/ui'
import { useNodePacksI18n } from '../i18n'

// ─── Shared types ─────────────────────────────────────────────────────────────

export interface DownloadInfo {
  percent: number
  file?: string
  fileIndex?: number
  totalFiles?: number
  status?: string
  bytesDownloaded?: number
  totalBytes?: number
  stalledSeconds?: number
  paused?: boolean
}

export type DownloadMap = Record<string, DownloadInfo>

/**
 * Return the durable weight resource id for a node.
 *
 * A model pack can expose multiple executable nodes backed by one shared
 * download directory (for example Trellis generate + refine). The manifest's
 * download.location is the resource identity; node ids remain workflow/runtime
 * identities and must not create separate weight downloads.
 */
export function getNodeDownloadId(ext: AnyNodePack, node: NodePackNode): string {
  if (ext.type === 'model' && ext.download?.location?.trim()) return ext.download.location.trim()
  return `${ext.id}/${node.id}`
}

export type NodeUiState =
  | { kind: 'ready' }
  | { kind: 'available' }
  | { kind: 'downloading'; dl: DownloadInfo }
  | { kind: 'installed' }

export function getNodeState(
  extId: string,
  node: NodePackNode,
  installedIds: string[],
  downloading: DownloadMap,
  downloadId = `${extId}/${node.id}`,
): NodeUiState {
  const fullId = `${extId}/${node.id}`
  if (!node.hfRepo) return { kind: 'ready' }
  const dl = downloading[downloadId]
  if (dl) return { kind: 'downloading', dl }
  if (installedIds.includes(fullId)) return { kind: 'installed' }
  return { kind: 'available' }
}

export function extInstallSummary(
  ext: AnyNodePack,
  installedIds: string[],
  downloading: DownloadMap,
): { total: number; done: number; installing: boolean; hasAvailable: boolean } {
  const states = ext.nodes.map((n) => getNodeState(ext.id, n, installedIds, downloading, getNodeDownloadId(ext, n)))
  return {
    total: states.length,
    done: states.filter((s) => s.kind === 'installed' || s.kind === 'ready').length,
    installing: states.some((s) => s.kind === 'downloading'),
    hasAvailable: states.some((s) => s.kind === 'available'),
  }
}

export function formatBytes(bytes?: number): string {
  if (!bytes || bytes <= 0) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB']
  let value = bytes
  let idx = 0
  while (value >= 1024 && idx < units.length - 1) {
    value /= 1024
    idx += 1
  }
  return `${value >= 10 || idx === 0 ? value.toFixed(0) : value.toFixed(1)} ${units[idx]}`
}

// ─── Shared visual language ───────────────────────────────────────────────────

export function TypePill({ type }: { type: 'model' | 'process' }): JSX.Element {
  const { t } = useNodePacksI18n()
  return (
    <Badge
      variant="outline"
      className={type === 'process'
        ? 'border-sky-500/25 bg-sky-500/10 text-[10px] font-semibold uppercase tracking-wider text-sky-400'
        : 'border-primary/30 bg-primary/10 text-[10px] font-semibold uppercase tracking-wider text-primary'}
    >
      {type === 'process' ? t('nodePacks.typeProcess') : t('nodePacks.typeModel')}
    </Badge>
  )
}

export function IOBadge({ node }: { node: NodePackNode }): JSX.Element {
  const inputs = node.inputs?.length ? node.inputs : [node.input]
  return (
    <span className="flex shrink-0 items-center gap-1 font-mono text-[10px] text-muted-foreground">
      {inputs.map((input, index) => (
        <Badge key={`${input}-${index}`} variant="secondary" className="px-1.5 py-0 font-mono text-[10px] font-normal">
          {input}
        </Badge>
      ))}
      <ArrowRight className="h-3 w-3 shrink-0 text-muted-foreground" strokeWidth={1.8} />
      <Badge variant="secondary" className="px-1.5 py-0 font-mono text-[10px] font-normal">
        {node.output}
      </Badge>
    </span>
  )
}

const LED_TONES = {
  green: 'bg-emerald-400 shadow-[0_0_0_3px_rgba(52,211,153,0.12)]',
  amber: 'bg-amber-400 shadow-[0_0_0_3px_rgba(251,191,36,0.12)]',
  brand: 'bg-primary shadow-[0_0_0_3px_rgba(93,148,217,0.18)] animate-pulse',
} as const

const TEXT_TONES = {
  green: 'text-emerald-400',
  amber: 'text-amber-400',
  brand: 'text-primary',
} as const

export function StatusBadge({ tone, children }: { tone: keyof typeof LED_TONES; children: React.ReactNode }): JSX.Element {
  return (
    <span className={`inline-flex items-center gap-2 text-[11.5px] font-medium ${TEXT_TONES[tone]}`}>
      <span className={`h-[7px] w-[7px] rounded-full ${LED_TONES[tone]}`} />
      {children}
    </span>
  )
}

export const ICONS = {
  cube: <Box className="h-full w-full" strokeWidth={1.5} />,
  spark: <Sparkles className="h-full w-full" strokeWidth={1.5} />,
  shield: <ShieldCheck className="h-full w-full" strokeWidth={2} />,
  download: <Download className="h-full w-full" strokeWidth={2} />,
  check: <Check className="h-full w-full" strokeWidth={2.5} />,
}

// ─── Per-node install control (shared between card and drawer) ────────────────

interface NodeControlProps {
  state: NodeUiState
  disabled?: boolean
  onInstall: () => void
  onPause: () => void
  onResume: () => void
  onCancel: () => void
}

export function NodeInstallControl({ state, disabled, onInstall, onPause, onResume, onCancel }: NodeControlProps): JSX.Element | null {
  const { t } = useNodePacksI18n()
  if (state.kind === 'ready') return null

  if (state.kind === 'installed') {
    return (
      <span className="inline-flex items-center gap-1.5 text-[11px] font-semibold text-emerald-400">
        <Check className="h-3 w-3" strokeWidth={2.5} />
        {t('nodePacks.installed')}
      </span>
    )
  }

  if (state.kind === 'downloading') {
    const { dl } = state
    const paused = dl.paused ?? false
    return (
      <span className="flex items-center gap-1.5">
        <span className="flex flex-col items-end gap-1">
          <span className="block h-1 w-[84px] overflow-hidden rounded-full bg-muted" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={dl.percent}>
            <span
              className={`block h-full rounded-full transition-all duration-300 ${paused ? 'bg-muted-foreground' : 'bg-primary'}`}
              style={{ width: `${dl.percent}%` }}
            />
          </span>
          <span className={`font-mono text-[10px] ${paused ? 'text-muted-foreground' : 'text-primary'}`}>
            {paused ? t('nodePacks.paused') : `${dl.percent}%`}
          </span>
        </span>
        <Button
          type="button"
          variant="ghost"
          size="icon"
          className="h-7 w-7 text-muted-foreground"
          onClick={(event) => { event.stopPropagation(); paused ? onResume() : onPause() }}
          title={paused ? t('nodePacks.resumeDownload') : t('nodePacks.pauseDownload')}
          aria-label={paused ? t('nodePacks.resumeDownload') : t('nodePacks.pauseDownload')}
        >
          {paused ? <Play className="h-3 w-3" fill="currentColor" /> : <Pause className="h-3 w-3" fill="currentColor" />}
        </Button>
        <Button
          type="button"
          variant="ghost"
          size="icon"
          className="h-7 w-7 text-muted-foreground hover:bg-destructive/10 hover:text-destructive"
          onClick={(event) => { event.stopPropagation(); onCancel() }}
          title={t('nodePacks.cancelDownload')}
          aria-label={t('nodePacks.cancelDownload')}
        >
          <X className="h-3 w-3" strokeWidth={2.5} />
        </Button>
      </span>
    )
  }

  return (
    <Button
      type="button"
      size="sm"
      className="h-7 gap-1.5 px-2.5"
      disabled={disabled}
      onClick={(event) => { event.stopPropagation(); onInstall() }}
    >
      <Download className="h-3 w-3" />
      {t('nodePacks.install')}
    </Button>
  )
}

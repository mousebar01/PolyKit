import { useEffect, useState } from 'react'
import { Box, Image as ImageIcon } from 'lucide-react'

import { useI18n } from '@shared/i18n'
import type { ProjectedAssetLibraryEntry } from '../assetLibraryProjection'
import { CAPABILITY_LABEL_KEYS } from '../assetLibraryLabels'

function resolveAssetUrl(url: string | undefined, thumbnailBase?: string): string | undefined {
  if (!url) return undefined
  if (/^(?:data:|blob:|https?:\/\/)/i.test(url) || !thumbnailBase) return url
  return `${thumbnailBase.replace(/\/+$/, '')}/${url.replace(/^\/+/, '')}`
}

export interface AssetLibraryEntryCardProps {
  entry: ProjectedAssetLibraryEntry
  /** Base URL for the FastAPI server when an entry contains a relative preview. */
  thumbnailBase?: string
  latest?: boolean
  active?: boolean
  onClick?: () => void
  actionLabel?: string
}

/**
 * Compact, responsive representation of an asset-library entry.
 *
 * This is shared by workflow outputs and other places that need to show a
 * server-owned artifact. Keeping the metadata and preview logic here prevents
 * output panels from drifting away from the asset library presentation.
 */
export function AssetLibraryEntryCard({
  entry,
  thumbnailBase,
  latest = false,
  active = false,
  onClick,
  actionLabel,
}: AssetLibraryEntryCardProps): JSX.Element {
  const { t } = useI18n()
  const thumbnailUrl = resolveAssetUrl(entry.thumbnail ?? entry.preview, thumbnailBase)
  const [thumbnailState, setThumbnailState] = useState<'loading' | 'loaded' | 'error'>(thumbnailUrl ? 'loading' : 'error')

  useEffect(() => {
    setThumbnailState(thumbnailUrl ? 'loading' : 'error')
  }, [thumbnailUrl])

  const capabilityKey = entry.capability ? CAPABILITY_LABEL_KEYS[entry.capability] : undefined
  const typeLabel = capabilityKey ? t(capabilityKey) : entry.previewKind
  const imageAsset = entry.capability === 'image'
  const content = (
    <>
      <span
        className={`relative flex h-10 w-10 shrink-0 items-center justify-center overflow-hidden rounded-md border transition-colors ${
          imageAsset ? 'alpha-checker' : 'bg-muted/60'
        } ${active ? 'border-primary/35 text-primary' : 'border-divider text-muted-foreground group-hover:border-primary/30 group-hover:text-foreground'}`}
        aria-hidden="true"
      >
        {(!thumbnailUrl || thumbnailState === 'error') && (
          imageAsset
            ? <ImageIcon className="size-4" strokeWidth={1.5} />
            : <Box className="size-4" strokeWidth={1.5} />
        )}
        {thumbnailUrl && (
          <img
            src={thumbnailUrl}
            alt=""
            loading="lazy"
            onLoad={() => setThumbnailState('loaded')}
            onError={() => setThumbnailState('error')}
            className={`absolute inset-0 size-full object-contain brightness-[0.96] contrast-[1.03] saturate-[0.96] transition-opacity duration-200 ${thumbnailState === 'error' ? 'hidden' : ''}`}
          />
        )}
      </span>

      <span className="min-w-0 flex-1">
        <span className="flex min-w-0 items-center gap-1.5">
          <span className="min-w-0 truncate text-[11px] font-medium text-foreground">{entry.displayName}</span>
          {latest && <span className="shrink-0 text-[9px] text-sky-400">{t('workflows.latest')}</span>}
        </span>
        <span className="mt-1 block min-w-0 truncate font-mono text-[10px] text-muted-foreground">{entry.workspacePath}</span>
      </span>
    </>
  )

  const footer = (
    <span className="mt-3 flex min-w-0 items-center justify-between gap-2 border-t border-divider pt-2">
      <span className="truncate text-[9px] uppercase tracking-wider text-muted-foreground">{typeLabel}</span>
      {actionLabel && (
        <span className="shrink-0 truncate text-[10px] text-primary opacity-0 transition-opacity group-hover:opacity-100">{actionLabel}</span>
      )}
    </span>
  )

  const className = `group flex w-full min-w-0 flex-wrap items-start rounded-lg border p-3 text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background ${
    active
      ? 'border-primary/45 bg-primary/10'
      : 'border-divider bg-card/70 hover:border-primary/30 hover:bg-muted/60'
  }`

  if (onClick) {
    return (
      <button type="button" onClick={onClick} className={className}>
        <span className="flex w-full min-w-0 items-start gap-2.5">{content}</span>
        {footer}
      </button>
    )
  }

  return (
    <div className={className}>
      <span className="flex w-full min-w-0 items-start gap-2.5">{content}</span>
      {footer}
    </div>
  )
}

export default AssetLibraryEntryCard

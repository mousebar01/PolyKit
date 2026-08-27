import { useEffect, useState } from 'react'
import { Activity } from 'lucide-react'

import { Button, Popover, PopoverContent, PopoverTrigger } from '@shared/components/ui'
import { useI18n } from '@shared/i18n'
import type { SystemGpuResource, SystemResourceSnapshot } from '@shared/types/systemResources'

const GB = 1024 ** 3
const VISIBLE_INTERVAL_MS = 2_000
const HIDDEN_INTERVAL_MS = 10_000

function formatPercent(value: number | null): string {
  return value === null ? '—' : `${Math.round(value)}%`
}

function formatGb(value: number | null): string {
  return value === null ? '—' : `${(value / GB).toFixed(1)}G`
}

function formatMemory(used: number | null, total: number | null): string {
  if (used === null || total === null) return '—'
  return `${(used / GB).toFixed(1)} / ${(total / GB).toFixed(1)} GB`
}

function MetricRow({ label, value }: { label: string; value: string }): JSX.Element {
  return (
    <div className="flex items-center justify-between gap-4 text-xs">
      <span className="text-muted-foreground">{label}</span>
      <span className="tabular-nums text-foreground">{value}</span>
    </div>
  )
}

function GpuDetails({ gpu }: { gpu: SystemGpuResource }): JSX.Element {
  const { t } = useI18n()
  return (
    <div className="space-y-2 border-t border-divider pt-3 first:border-t-0 first:pt-0">
      <div className="truncate text-xs font-medium text-foreground" title={gpu.name}>
        {gpu.name}
      </div>
      <MetricRow label="GPU" value={formatPercent(gpu.usage)} />
      <MetricRow label="VRAM" value={formatMemory(gpu.memory.used, gpu.memory.total)} />
      <MetricRow
        label={t('top.temperature')}
        value={gpu.temperature === null ? '—' : `${gpu.temperature}°C`}
      />
    </div>
  )
}

export default function ResourceIndicator({ apiUrl }: { apiUrl: string }): JSX.Element | null {
  const { t } = useI18n()
  const [snapshot, setSnapshot] = useState<SystemResourceSnapshot | null>(null)

  useEffect(() => {
    if (!apiUrl) return

    let cancelled = false
    let timer: number | undefined
    let controller: AbortController | null = null

    const schedule = () => {
      if (cancelled) return
      if (timer !== undefined) window.clearTimeout(timer)
      const delay = document.hidden ? HIDDEN_INTERVAL_MS : VISIBLE_INTERVAL_MS
      timer = window.setTimeout(tick, delay)
    }

    const tick = async () => {
      if (cancelled) return
      controller?.abort()
      controller = new AbortController()
      try {
        const base = apiUrl.replace(/\/+$/, '')
        const response = await fetch(`${base}/system/resources`, {
          cache: 'no-store',
          signal: controller.signal,
        })
        if (!response.ok) throw new Error(`Resource endpoint returned ${response.status}`)
        const next = await response.json() as SystemResourceSnapshot
        if (!cancelled) setSnapshot(next)
      } catch (error) {
        if (!cancelled && !(error instanceof DOMException && error.name === 'AbortError')) {
          // Resource monitoring is optional UI. Keep the last good sample silently.
        }
      } finally {
        schedule()
      }
    }

    const onVisibilityChange = () => {
      if (timer !== undefined) window.clearTimeout(timer)
      // Refresh immediately when returning to the app; otherwise reduce polling.
      if (document.hidden) schedule()
      else void tick()
    }

    void tick()
    document.addEventListener('visibilitychange', onVisibilityChange)
    return () => {
      cancelled = true
      controller?.abort()
      if (timer !== undefined) window.clearTimeout(timer)
      document.removeEventListener('visibilitychange', onVisibilityChange)
    }
  }, [apiUrl])

  if (!snapshot) return null

  const primaryGpu = snapshot.gpus[0]

  return (
    <Popover>
      <PopoverTrigger asChild>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          className="no-drag mr-2 h-7 gap-2 rounded-md bg-transparent px-2.5 text-[10px] font-normal text-muted-foreground hover:bg-muted/35"
          aria-label={t('top.resources')}
        >
          <Activity className="size-3.5" />
          <span>CPU <span className="tabular-nums text-foreground">{formatPercent(snapshot.cpu.usage)}</span></span>
          <span>RAM <span className="tabular-nums text-foreground">{formatGb(snapshot.memory.used)}</span></span>
          {primaryGpu && (
            <>
              <span>GPU <span className="tabular-nums text-foreground">{formatPercent(primaryGpu.usage)}</span></span>
              <span className="hidden 2xl:inline">VRAM <span className="tabular-nums text-foreground">{formatGb(primaryGpu.memory.used)}</span></span>
            </>
          )}
        </Button>
      </PopoverTrigger>
      <PopoverContent align="end" className="w-72 space-y-3 p-4">
        <div className="text-xs font-semibold text-foreground">{t('top.resources')}</div>
        <div className="space-y-2">
          <MetricRow label="CPU" value={formatPercent(snapshot.cpu.usage)} />
          <MetricRow label={t('top.cores')} value={snapshot.cpu.cores === null ? '—' : String(snapshot.cpu.cores)} />
          <MetricRow label="RAM" value={formatMemory(snapshot.memory.used, snapshot.memory.total)} />
        </div>
        {snapshot.gpus.length > 0 ? (
          <div className="space-y-3">
            {snapshot.gpus.map((gpu) => <GpuDetails key={gpu.index} gpu={gpu} />)}
          </div>
        ) : (
          <div className="border-t border-divider pt-3 text-xs text-muted-foreground">
            {t('top.gpuUnavailable')}
          </div>
        )}
      </PopoverContent>
    </Popover>
  )
}

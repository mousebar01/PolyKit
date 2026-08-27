import { useEffect, useRef, useState } from 'react'
import { Check, Copy, X } from 'lucide-react'

import { Button, Card } from '@shared/components/ui'
import { useGeneration } from '@shared/hooks/useGeneration'
import { useI18n, type TranslationKey } from '@shared/i18n'

function formatElapsed(seconds: number): string {
  const minutes = Math.floor(seconds / 60)
  const remainder = seconds % 60
  return `${minutes.toString().padStart(2, '0')}:${remainder.toString().padStart(2, '0')}`
}

const PHASE_VERB: Array<[RegExp, TranslationKey]> = [
  [/diffusion|sampling/i, 'assets.hudGenerating'],
  [/volume|flashvdm|decoding/i, 'assets.hudDecoding'],
  [/surface|extract/i, 'assets.hudExtracting'],
  [/loading|download/i, 'assets.hudLoading'],
]

interface TqdmStatus {
  verbKey: TranslationKey | null
  pct: string
}

function parseTqdmLine(line: string): TqdmStatus | null {
  const match = line.match(/^(.+?):\s*(\d+)%\|/)
  if (!match) return null
  const description = match[1].replace(/\.+$/, '').trim()
  const pct = match[2]
  const verbKey = PHASE_VERB.find(([pattern]) => pattern.test(description))?.[1] ?? null
  return { verbKey, pct }
}

function parseProgressFromStderr(chunk: string): TqdmStatus | null {
  const lines = chunk.split(/[\r\n]+/).filter(Boolean)
  for (let index = lines.length - 1; index >= 0; index--) {
    const parsed = parseTqdmLine(lines[index]!)
    if (parsed) return parsed
  }
  return null
}

export default function GenerationHUD(): JSX.Element | null {
  const { currentJob, reset } = useGeneration()
  const [elapsed, setElapsed] = useState(0)
  const [tqdmLog, setTqdmLog] = useState<TqdmStatus | null>(null)
  const [copied, setCopied] = useState(false)
  const copyTimeout = useRef<ReturnType<typeof setTimeout> | null>(null)
  const { t } = useI18n()

  function handleCopyError(text: string) {
    void navigator.clipboard.writeText(text)
    setCopied(true)
    if (copyTimeout.current) clearTimeout(copyTimeout.current)
    copyTimeout.current = setTimeout(() => setCopied(false), 2000)
  }

  const status = currentJob?.status
  const isActive = status === 'uploading' || status === 'generating'
  const isVisible = isActive || status === 'error'

  useEffect(() => {
    if (isActive && currentJob?.createdAt) {
      const intervalId = setInterval(() => {
        setElapsed(Math.floor((Date.now() - currentJob.createdAt) / 1000))
      }, 1000)
      return () => clearInterval(intervalId)
    }
    setElapsed(0)
  }, [isActive, currentJob?.createdAt])

  useEffect(() => {
    if (!isActive) return

    setTqdmLog(null)
    window.polykit.python.onLog((line) => {
      const parsed = parseProgressFromStderr(line)
      if (parsed !== null) setTqdmLog(parsed)
    })
    return () => {
      window.polykit.python.offLog()
      setTqdmLog(null)
    }
  }, [isActive])

  useEffect(() => () => {
    if (copyTimeout.current) clearTimeout(copyTimeout.current)
  }, [])

  if (!currentJob || !isVisible) return null

  const { progress, step, error } = currentJob

  return (
    <div className="pointer-events-auto absolute bottom-8 left-1/2 z-20 w-[calc(100%-2rem)] max-w-96 -translate-x-1/2 animate-slide-up">
      <Card className="overflow-hidden border-divider bg-card/95 backdrop-blur-md">
        {isActive && (
          <div className="flex flex-col gap-3 px-5 py-4">
            <div className="flex items-center justify-between gap-4">
              <div className="flex min-w-0 items-center gap-2.5">
                <span className="h-1.5 w-1.5 shrink-0 animate-pulse rounded-full bg-primary" aria-hidden="true" />
                <span className="truncate text-sm font-medium text-foreground">
                  {step ?? (status === 'uploading' ? t('assets.hudReading') : t('assets.hudGeneratingMesh'))}
                </span>
              </div>
              <span className="shrink-0 text-xs tabular-nums text-muted-foreground">{formatElapsed(elapsed)}</span>
            </div>

            <div className="flex flex-col gap-1.5">
              <div
                className="h-1.5 overflow-hidden rounded-full bg-muted"
                role="progressbar"
                aria-label={t('assets.hudGenerating')}
                aria-valuemin={0}
                aria-valuemax={100}
                aria-valuenow={progress}
              >
                <div className="h-full rounded-full bg-primary transition-all duration-700 ease-out" style={{ width: `${progress}%` }} />
              </div>
              <div className="flex items-center justify-between gap-2">
                {tqdmLog && (
                  <span className="truncate font-mono text-[11px] text-muted-foreground">
                    {tqdmLog.verbKey ? t(tqdmLog.verbKey) : ''} ({tqdmLog.pct}%)
                  </span>
                )}
                <span className="ml-auto shrink-0 text-xs tabular-nums text-muted-foreground">{progress}%</span>
              </div>
            </div>
          </div>
        )}

        {status === 'error' && (
          <div className="flex animate-fade-in flex-col gap-3 px-5 py-4">
            <div className="flex items-center gap-2.5">
              <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full border border-destructive/25 bg-destructive/10 text-destructive">
                <X className="h-3.5 w-3.5" />
              </span>
              <span className="text-sm font-medium text-foreground">{t('assets.generationFailed')}</span>
            </div>
            <pre className="max-h-48 select-text overflow-y-auto whitespace-pre-wrap break-words rounded-md border border-destructive/25 bg-destructive/10 px-3 py-2 font-mono text-xs leading-relaxed text-destructive">
              {error}
            </pre>
            <div className="flex gap-2">
              <Button type="button" variant="outline" className="flex-1" onClick={reset}>
                {t('assets.tryAgain')}
              </Button>
              {error && (
                <Button
                  type="button"
                  variant="outline"
                  className="gap-1.5"
                  onClick={() => handleCopyError(error)}
                  title={t('assets.copy')}
                >
                  {copied ? <Check className="h-4 w-4 text-emerald-400" /> : <Copy className="h-4 w-4" />}
                  <span className={copied ? 'text-emerald-400' : undefined}>{copied ? t('assets.copied') : t('assets.copy')}</span>
                </Button>
              )}
            </div>
          </div>
        )}
      </Card>
    </div>
  )
}

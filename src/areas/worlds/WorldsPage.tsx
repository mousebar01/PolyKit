import { useMemo, useState } from 'react'
import { Globe2, Layers3, LoaderCircle, RefreshCw, Save, Sparkles, Workflow } from 'lucide-react'

import { Badge } from '@shared/components/ui/badge'
import { Button } from '@shared/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@shared/components/ui/card'
import { Input } from '@shared/components/ui/input'
import { useAppStore } from '@shared/stores/appStore'
import { useNavStore } from '@shared/stores/navStore'
import { useI18n } from '@shared/i18n'
import WorldCanvas from './components/WorldCanvas'
import { useWorldStore } from './worldStore'

function formatNumber(value: number): string {
  return new Intl.NumberFormat(undefined, { maximumFractionDigits: 0 }).format(value)
}

export default function WorldsPage(): JSX.Element {
  const { t } = useI18n()
  const apiUrl = useAppStore((state) => state.apiUrl)
  const navigate = useNavStore((state) => state.navigate)
  const {
    document, terrain, instances, selectedProtoId, saving, loading, error,
    setSelectedProtoId, save, load, clearError,
  } = useWorldStore()
  const [worldId, setWorldId] = useState(document.id)

  const heroCount = useMemo(
    () => document.spec.assets.filter((asset) => asset.tier === 'hero').length,
    [document.spec.assets],
  )
  const regionSummary = useMemo(
    () => document.spec.regions.map((region) => ({ ...region, count: instances.filter((item) => item.regionId === region.id).length })),
    [document.spec.regions, instances],
  )

  async function handleSave(): Promise<void> {
    await save()
  }

  async function handleLoad(): Promise<void> {
    if (!worldId.trim()) return
    await load(worldId)
  }

  return (
    <section className="flex min-h-0 flex-1 flex-col gap-3 overflow-hidden" aria-labelledby="worlds-title">
      <header className="flex shrink-0 items-center justify-between gap-4 px-1">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <Globe2 className="size-5 text-primary" aria-hidden="true" />
            <h1 id="worlds-title" className="text-2xl font-semibold tracking-tight">{t('worlds.title')}</h1>
            <Badge variant="secondary">{t('worlds.localRuntime')}</Badge>
          </div>
          <p className="mt-1 truncate text-xs text-muted-foreground">{document.spec.logline}</p>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <Button type="button" variant="outline" size="sm" onClick={() => navigate('workflows')}>
            <Workflow className="mr-1.5 size-4" aria-hidden="true" />
            {t('worlds.openWorkflows')}
          </Button>
          <Button type="button" size="sm" onClick={() => void handleSave()} disabled={saving || !apiUrl}>
            {saving ? <LoaderCircle className="mr-1.5 size-4 animate-spin" aria-hidden="true" /> : <Save className="mr-1.5 size-4" aria-hidden="true" />}
            {saving ? t('worlds.saving') : t('worlds.save')}
          </Button>
        </div>
      </header>

      <div className="grid min-h-0 flex-1 grid-cols-[minmax(0,1fr)_290px] gap-3">
        <div className="relative min-h-0 overflow-hidden rounded-lg bg-muted">
          <WorldCanvas
            spec={document.spec}
            terrain={terrain}
            instances={instances}
            selectedProtoId={selectedProtoId}
          />
          <div className="pointer-events-none absolute left-3 top-3 flex items-center gap-2 rounded-md bg-background/80 px-2.5 py-1.5 text-[11px] text-muted-foreground backdrop-blur-sm">
            <span className="size-1.5 rounded-full bg-primary" aria-hidden="true" />
            {t('worlds.seed')} {document.spec.seed}
            <span className="text-divider">·</span>
            {formatNumber(instances.length)} {t('worlds.instances')}
          </div>
          <div className="pointer-events-none absolute bottom-3 left-3 rounded-md bg-background/75 px-2.5 py-1.5 text-[11px] text-muted-foreground backdrop-blur-sm">
            {t('worlds.controls')}
          </div>
        </div>

        <aside className="flex min-h-0 flex-col gap-3 overflow-y-auto" aria-label="World inspector">
          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="flex items-center gap-2"><Layers3 className="size-4 text-primary" aria-hidden="true" /> {t('worlds.snapshot')}</CardTitle>
              <CardDescription>{t('worlds.snapshotDescription')}</CardDescription>
            </CardHeader>
            <CardContent className="grid grid-cols-2 gap-2 text-xs">
              <div className="rounded-md bg-muted p-2"><span className="block text-muted-foreground">{t('worlds.mapSize')}</span><span className="font-mono text-foreground">{document.spec.size} m</span></div>
              <div className="rounded-md bg-muted p-2"><span className="block text-muted-foreground">{t('worlds.regions')}</span><span className="font-mono text-foreground">{document.spec.regions.length}</span></div>
              <div className="rounded-md bg-muted p-2"><span className="block text-muted-foreground">Instances</span><span className="font-mono text-foreground">{formatNumber(instances.length)}</span></div>
              <div className="rounded-md bg-muted p-2"><span className="block text-muted-foreground">{t('worlds.heroSlots')}</span><span className="font-mono text-foreground">{heroCount}</span></div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="pb-3"><CardTitle>{t('worlds.regions')}</CardTitle></CardHeader>
            <CardContent className="space-y-1.5">
              {regionSummary.map((region) => (
                <button
                  key={region.id}
                  type="button"
                  className="flex w-full items-center justify-between rounded-md px-2 py-1.5 text-left text-xs transition-colors hover:bg-muted focus-visible:ring-2 focus-visible:ring-ring"
                  onClick={() => setSelectedProtoId(null)}
                >
                  <span className="flex min-w-0 items-center gap-2">
                    <span className="size-2.5 shrink-0 rounded-sm" style={{ backgroundColor: region.material.color }} aria-hidden="true" />
                    <span className="truncate">{region.name}</span>
                  </span>
                  <span className="font-mono text-[11px] text-muted-foreground">{region.count}</span>
                </button>
              ))}
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="pb-3"><CardTitle>{t('worlds.prototypes')}</CardTitle><CardDescription>{t('worlds.prototypeHint')}</CardDescription></CardHeader>
            <CardContent className="space-y-1.5">
              {document.spec.assets.map((asset) => {
                const count = instances.filter((instance) => instance.protoId === asset.id).length
                const selected = selectedProtoId === asset.id
                return (
                  <button
                    key={asset.id}
                    type="button"
                    aria-pressed={selected}
                    className={`flex w-full items-center justify-between rounded-md px-2 py-1.5 text-left text-xs transition-colors hover:bg-muted focus-visible:ring-2 focus-visible:ring-ring ${selected ? 'bg-primary/10 text-primary' : ''}`}
                    onClick={() => setSelectedProtoId(selected ? null : asset.id)}
                  >
                    <span className="flex min-w-0 items-center gap-2">
                      <Sparkles className="size-3.5 shrink-0" aria-hidden="true" />
                      <span className="truncate">{asset.name}</span>
                    </span>
                    <span className="font-mono text-[11px] text-muted-foreground">{count}</span>
                  </button>
                )
              })}
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="pb-3"><CardTitle>{t('worlds.openSaved')}</CardTitle><CardDescription>{t('worlds.openSavedDescription')}</CardDescription></CardHeader>
            <CardContent className="space-y-2">
              <label htmlFor="world-id" className="text-xs font-medium">{t('worlds.worldId')}</label>
              <div className="flex gap-2">
                <Input id="world-id" value={worldId} onChange={(event) => setWorldId(event.target.value)} onKeyDown={(event) => { if (event.key === 'Enter') void handleLoad() }} />
                <Button type="button" variant="outline" size="icon" title={t('worlds.load')} aria-label={t('worlds.load')} onClick={() => void handleLoad()} disabled={loading}>
                  {loading ? <LoaderCircle className="size-4 animate-spin" aria-hidden="true" /> : <RefreshCw className="size-4" aria-hidden="true" />}
                </Button>
              </div>
              {!apiUrl && <p className="text-[11px] text-muted-foreground">{t('worlds.connectServer')}</p>}
              {error && (
                <button type="button" className="w-full rounded-md bg-destructive/10 px-2 py-1.5 text-left text-[11px] text-destructive" onClick={clearError}>
                  {error}
                </button>
              )}
            </CardContent>
          </Card>
        </aside>
      </div>
    </section>
  )
}

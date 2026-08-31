import { useMemo, useState } from 'react'
import { Info, Layers3, LoaderCircle, Save, Sparkles, X } from 'lucide-react'

import { Badge, Button, Card, CardContent, CardDescription, CardHeader, CardTitle } from '@shared/components/ui'
import { useAppStore } from '@shared/stores/appStore'
import { useI18n } from '@shared/i18n'
import { isRenderableWorldSpec } from '../runtime/types'
import { isRenderableScenePlan } from '../runtime/scenePlan'
import WorldCanvas, { WORLD_VIEWER_BACKGROUND_COLOR } from './WorldCanvas'
import ScenePlanCanvas from './ScenePlanCanvas'
import { useWorldStore } from '../worldStore'

function formatNumber(value: number): string {
  return new Intl.NumberFormat(undefined, { maximumFractionDigits: 0 }).format(value)
}

interface WorldAssetViewerProps {
  onClose: () => void
}

/** Focused editor for a strict schema-v2 world runtime. */
export default function WorldAssetViewer({ onClose }: WorldAssetViewerProps): JSX.Element {
  const { t } = useI18n()
  const apiUrl = useAppStore((state) => state.apiUrl)
  const {
    document, terrain, instances, selectedProtoId, saving, error,
    setSelectedProtoId, save, clearError,
  } = useWorldStore()
  const [infoOpen, setInfoOpen] = useState(false)

  const buildSpec = document?.runtime.build
  const scenePlan = document?.runtime.scene
  const renderableBuild = isRenderableWorldSpec(buildSpec)
  const renderableScene = isRenderableScenePlan(scenePlan)
  const scenePrompt = renderableScene ? scenePlan.prompt : undefined

  const heroCount = useMemo(
    () => renderableBuild ? buildSpec.assets.filter((asset) => asset.tier === 'hero').length : 0,
    [buildSpec, renderableBuild],
  )
  const regionSummary = useMemo(
    () => renderableBuild
      ? buildSpec.regions.map((region) => ({
        ...region,
        count: instances.filter((item) => item.regionId === region.id).length,
      }))
      : [],
    [buildSpec, instances, renderableBuild],
  )

  if (!document) return <div className="h-full min-h-0 flex-1 bg-card" />

  return (
    <div className="flex h-full min-h-0 min-w-0 flex-1 overflow-hidden bg-card">
      <div className="relative min-h-0 min-w-0 flex-1 overflow-hidden bg-card">
        {renderableBuild && terrain ? (
          <WorldCanvas
            spec={buildSpec}
            terrain={terrain}
            instances={instances}
            selectedProtoId={selectedProtoId}
            artifacts={document.artifacts}
            backgroundColor={WORLD_VIEWER_BACKGROUND_COLOR}
          />
        ) : renderableScene ? (
          <ScenePlanCanvas plan={scenePlan} artifacts={document.artifacts} backgroundColor={WORLD_VIEWER_BACKGROUND_COLOR} />
        ) : (
          <div className="flex h-full flex-col items-center justify-center gap-3 px-6 text-center">
            <Sparkles className="size-7 text-primary/75" strokeWidth={1.5} aria-hidden="true" />
            <div>
              <p className="text-sm font-medium text-foreground">{t('worlds.planningScene')}</p>
              <p className="mt-1 text-xs text-muted-foreground">{t('worlds.planningSceneDescription')}</p>
            </div>
            <p className="max-w-xs break-all font-mono text-[10px] text-muted-foreground/70">{document.id}</p>
          </div>
        )}

        <div className="absolute left-3 top-3 flex max-w-[calc(100%-24px)] items-center gap-2 rounded-md border border-divider bg-background/85 px-2.5 py-1.5 backdrop-blur-sm">
          <span className="size-1.5 shrink-0 rounded-full bg-primary" aria-hidden="true" />
          <span className="truncate text-xs font-medium text-foreground">{document.name}</span>
          <Badge variant="secondary" className="shrink-0 text-[10px]">{t('assets.capabilityGeneratedWorlds')}</Badge>
        </div>

        <div className="absolute right-3 top-3 flex items-center gap-1">
          <Button
            type="button"
            variant="ghost"
            size="icon"
            className="size-7 bg-background/80 text-muted-foreground backdrop-blur-sm hover:bg-background hover:text-foreground"
            onClick={() => setInfoOpen((open) => !open)}
            title={t('worlds.sceneInfo')}
            aria-label={t('worlds.sceneInfo')}
            aria-expanded={infoOpen}
          >
            <Info className="size-3.5" aria-hidden="true" />
          </Button>
          <Button
            type="button"
            variant="ghost"
            size="icon"
            className="size-7 bg-background/80 text-muted-foreground backdrop-blur-sm hover:bg-background hover:text-foreground"
            onClick={onClose}
            title={t('common.close')}
            aria-label={t('common.close')}
          >
            <X className="size-3.5" aria-hidden="true" />
          </Button>
        </div>

        {infoOpen && (
          <div className="absolute right-3 top-12 z-20 w-[min(280px,calc(100%-24px))] rounded-md border border-divider bg-background/90 px-3 py-2.5 text-[11px] text-muted-foreground backdrop-blur-sm">
            <p className="truncate font-medium text-foreground">{document.name}</p>
            <p className="mt-1 truncate font-mono text-[10px] text-muted-foreground/80">{document.id}</p>
            {(renderableBuild || renderableScene) && <p className="mt-2 border-t border-divider pt-2 text-[10px] leading-relaxed text-muted-foreground/85">{t('worlds.controls')}</p>}
          </div>
        )}
      </div>

      <aside className="flex w-[270px] min-h-0 shrink-0 flex-col gap-3 overflow-y-auto border-l border-divider bg-card/70 p-3" aria-label={t('worlds.inspector')}>
        <div className="flex items-center justify-between gap-2">
          <div className="min-w-0">
            <p className="truncate text-sm font-semibold text-foreground">{t('worlds.scene')}</p>
            <p className="truncate text-[11px] text-muted-foreground">{(renderableBuild ? buildSpec.logline : scenePrompt) || document.name}</p>
          </div>
          <Button type="button" size="sm" className="h-8 shrink-0 gap-1.5" onClick={() => void save()} disabled={saving || !apiUrl}>
            {saving ? <LoaderCircle className="size-3.5 animate-spin" aria-hidden="true" /> : <Save className="size-3.5" aria-hidden="true" />}
            {saving ? t('worlds.saving') : t('worlds.save')}
          </Button>
        </div>

        {renderableBuild ? (
          <>
            <Card>
              <CardHeader className="pb-3">
                <CardTitle className="flex items-center gap-2 text-sm"><Layers3 className="size-4 text-primary" aria-hidden="true" /> {t('worlds.snapshot')}</CardTitle>
                <CardDescription>{t('worlds.snapshotDescription')}</CardDescription>
              </CardHeader>
              <CardContent className="grid grid-cols-2 gap-2 text-xs">
                <div className="rounded-md bg-muted p-2"><span className="block text-muted-foreground">{t('worlds.mapSize')}</span><span className="font-mono text-foreground">{buildSpec.size} m</span></div>
                <div className="rounded-md bg-muted p-2"><span className="block text-muted-foreground">{t('worlds.regions')}</span><span className="font-mono text-foreground">{buildSpec.regions.length}</span></div>
                <div className="rounded-md bg-muted p-2"><span className="block text-muted-foreground">{t('worlds.instances')}</span><span className="font-mono text-foreground">{formatNumber(instances.length)}</span></div>
                <div className="rounded-md bg-muted p-2"><span className="block text-muted-foreground">{t('worlds.heroSlots')}</span><span className="font-mono text-foreground">{heroCount}</span></div>
              </CardContent>
            </Card>

            <Card>
              <CardHeader className="pb-3"><CardTitle className="text-sm">{t('worlds.regions')}</CardTitle></CardHeader>
              <CardContent className="space-y-1.5">
                {regionSummary.map((region) => (
                  <button
                    key={region.id}
                    type="button"
                    className="flex w-full items-center justify-between rounded-md px-2 py-1.5 text-left text-xs transition-colors hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
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
              <CardHeader className="pb-3"><CardTitle className="text-sm">{t('worlds.prototypes')}</CardTitle><CardDescription>{t('worlds.prototypeHint')}</CardDescription></CardHeader>
              <CardContent className="space-y-1.5">
                {buildSpec.assets.map((asset) => {
                  const count = instances.filter((instance) => instance.protoId === asset.id).length
                  const selected = selectedProtoId === asset.id
                  return (
                    <button
                      key={asset.id}
                      type="button"
                      aria-pressed={selected}
                      className={`flex w-full items-center justify-between rounded-md px-2 py-1.5 text-left text-xs transition-colors hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring ${selected ? 'bg-primary/10 text-primary' : ''}`}
                      onClick={() => setSelectedProtoId(selected ? null : asset.id)}
                    >
                      <span className="flex min-w-0 items-center gap-2"><Sparkles className="size-3.5 shrink-0" aria-hidden="true" /><span className="truncate">{asset.name}</span></span>
                      <span className="font-mono text-[11px] text-muted-foreground">{count}</span>
                    </button>
                  )
                })}
              </CardContent>
            </Card>
          </>
        ) : renderableScene ? (
          <>
            <Card>
              <CardHeader className="pb-3">
                <CardTitle className="flex items-center gap-2 text-sm"><Layers3 className="size-4 text-primary" aria-hidden="true" /> {t('worlds.snapshot')}</CardTitle>
                <CardDescription>{scenePlan.sceneKind || 'indoor'} · semantic scene plan</CardDescription>
              </CardHeader>
              <CardContent className="grid grid-cols-2 gap-2 text-xs">
                <div className="rounded-md bg-muted p-2"><span className="block text-muted-foreground">Bounds</span><span className="font-mono text-foreground">{scenePlan.bounds.width} × {scenePlan.bounds.depth} m</span></div>
                <div className="rounded-md bg-muted p-2"><span className="block text-muted-foreground">Objects</span><span className="font-mono text-foreground">{formatNumber(scenePlan.objects.length)}</span></div>
                <div className="rounded-md bg-muted p-2"><span className="block text-muted-foreground">Instances</span><span className="font-mono text-foreground">{formatNumber(scenePlan.instances.length)}</span></div>
                <div className="rounded-md bg-muted p-2"><span className="block text-muted-foreground">Seed</span><span className="font-mono text-foreground">{scenePlan.seed ?? 0}</span></div>
              </CardContent>
            </Card>
            <Card>
              <CardHeader className="pb-3"><CardTitle className="text-sm">Scene objects</CardTitle></CardHeader>
              <CardContent className="space-y-1.5">
                {scenePlan.objects.map((object) => (
                  <div key={object.id} className="flex items-center justify-between rounded-md px-2 py-1.5 text-xs">
                    <span className="flex min-w-0 items-center gap-2"><Sparkles className="size-3.5 shrink-0" aria-hidden="true" /><span className="truncate">{object.name}</span></span>
                    <span className="font-mono text-[11px] text-muted-foreground">{object.role}</span>
                  </div>
                ))}
              </CardContent>
            </Card>
          </>
        ) : null}

        {!apiUrl && <p className="text-[11px] text-muted-foreground">{t('worlds.connectServer')}</p>}
        {error && (
          <button type="button" className="w-full rounded-md bg-destructive/10 px-2 py-1.5 text-left text-[11px] text-destructive" onClick={clearError}>
            {error}
          </button>
        )}
      </aside>
    </div>
  )
}

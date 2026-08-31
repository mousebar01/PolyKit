import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Info, LoaderCircle, RefreshCw, Sparkles } from 'lucide-react'

import { Button } from '@shared/components/ui/button'
import { useAppStore } from '@shared/stores/appStore'
import { useI18n } from '@shared/i18n'
import WorldCanvas from '@areas/worlds/components/WorldCanvas'
import ScenePlanCanvas from '@areas/worlds/components/ScenePlanCanvas'
import { buildTerrain, type BuiltTerrain } from '@areas/worlds/runtime/terrain'
import { solvePlacements } from '@areas/worlds/runtime/placement'
import { isRenderableScenePlan } from '@areas/worlds/runtime/scenePlan'
import { currentRuntimeStage, type WorldRuntimeStageStatus } from '@areas/worlds/runtime/runtime'
import { isRenderableWorldSpec } from '@areas/worlds/runtime/types'
import type { WorldDocument } from '@areas/worlds/types'
import { listWorlds, loadWorld, type WorldSummary } from '@areas/worlds/worldApi'
import Viewer3D from '@areas/assets/components/Viewer3D'
import { WORLD_VIEWER_BACKGROUND_COLOR } from '@areas/worlds/components/WorldCanvas'

const REFRESH_INTERVAL_MS = 2500

interface AgentScenePreviewProps {
  width: number
}

function stageLabel(status: WorldRuntimeStageStatus | undefined, zh: boolean): string {
  if (status === 'passed') return zh ? '已通过' : 'Passed'
  if (status === 'running') return zh ? '进行中' : 'Running'
  if (status === 'failed') return zh ? '未通过' : 'Failed'
  if (status === 'ready') return zh ? '可执行' : 'Ready'
  return zh ? '锁定' : 'Locked'
}

export default function AgentScenePreview({ width }: AgentScenePreviewProps): JSX.Element {
  const { language, t } = useI18n()
  const zh = language === 'zh-CN'
  const apiUrl = useAppStore((state) => state.apiUrl)
  const [summary, setSummary] = useState<WorldSummary | null>(null)
  const [document, setDocument] = useState<WorldDocument | null>(null)
  const [terrain, setTerrain] = useState<BuiltTerrain | null>(null)
  const [instances, setInstances] = useState<WorldDocument['runtime']['compiled']['instances']>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [infoOpen, setInfoOpen] = useState(false)
  const loadingRef = useRef(false)
  const lastLoadedRef = useRef<string>('')

  const refresh = useCallback(async () => {
    if (!apiUrl || loadingRef.current) return
    loadingRef.current = true
    setLoading(true)
    try {
      const worlds = await listWorlds()
      const next = worlds[0] ?? null
      setSummary(next)
      if (!next) {
        setDocument(null)
        setTerrain(null)
        setInstances([])
        lastLoadedRef.current = ''
        setError(null)
        return
      }

      const revision = `${next.id}:${next.updatedAt}`
      if (revision === lastLoadedRef.current) {
        setError(null)
        return
      }

      const nextDocument = await loadWorld(next.id)
      setDocument(nextDocument)
      const build = nextDocument.runtime.build
      if (isRenderableWorldSpec(build)) {
        const nextTerrain = buildTerrain(build, { resolution: 72 })
        setTerrain(nextTerrain)
        const configured = nextDocument.runtime.compiled.instances
        setInstances(configured.length > 0 ? configured : solvePlacements(build, nextTerrain))
      } else {
        setTerrain(null)
        setInstances([])
      }
      lastLoadedRef.current = revision
      setError(null)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      loadingRef.current = false
      setLoading(false)
    }
  }, [apiUrl])

  useEffect(() => {
    void refresh()
    const interval = window.setInterval(() => void refresh(), REFRESH_INTERVAL_MS)
    return () => window.clearInterval(interval)
  }, [refresh])

  const stage = useMemo(
    () => document ? currentRuntimeStage(document.runtime.state) : null,
    [document],
  )
  const buildSpec = document?.runtime.build
  const scenePlan = document?.runtime.scene
  const renderableBuild = Boolean(document && terrain && isRenderableWorldSpec(buildSpec))
  const renderableScene = isRenderableScenePlan(scenePlan)

  return (
    <aside
      className="flex min-h-0 min-w-[320px] shrink-0 flex-col overflow-hidden bg-card/25"
      style={{ flexBasis: `${width}%`, width: `${width}%` }}
      aria-label={zh ? '当前场景预览' : 'Current scene preview'}
    >
      <div className="relative min-h-0 flex-1 overflow-hidden bg-card">
        <div className="absolute right-3 top-3 z-30 flex items-center gap-1">
          <Button
            type="button"
            variant="ghost"
            size="icon"
            className="size-7 bg-background/75 text-muted-foreground backdrop-blur-sm hover:bg-background hover:text-foreground"
            onClick={() => setInfoOpen((open) => !open)}
            title={zh ? '显示场景信息' : 'Show scene info'}
            aria-label={zh ? '显示场景信息' : 'Show scene info'}
            aria-expanded={infoOpen}
          >
            <Info className="size-3.5" aria-hidden="true" />
          </Button>
          <Button
            type="button"
            variant="ghost"
            size="icon"
            className="size-7 bg-background/75 text-muted-foreground backdrop-blur-sm hover:bg-background hover:text-foreground"
            onClick={() => void refresh()}
            disabled={loading}
            title={zh ? '刷新场景' : 'Refresh scene'}
            aria-label={zh ? '刷新场景' : 'Refresh scene'}
          >
            {loading ? <LoaderCircle className="size-3.5 animate-spin" aria-hidden="true" /> : <RefreshCw className="size-3.5" aria-hidden="true" />}
          </Button>
        </div>

        {infoOpen && (
          <div className="absolute right-3 top-12 z-30 w-[min(280px,calc(100%-24px))] rounded-md border border-divider bg-background/90 px-3 py-2.5 text-[11px] text-muted-foreground backdrop-blur-sm">
            <p className="truncate font-medium text-foreground">{document?.name ?? summary?.name ?? (zh ? '等待 Agent 规划场景' : 'Waiting for the Agent to plan a scene')}</p>
            <p className="mt-1 truncate font-mono text-[10px] text-muted-foreground/80">{document?.id ?? summary?.id ?? (zh ? '尚未创建场景' : 'No scene created')}</p>
            {(renderableBuild || renderableScene) && <p className="mt-2 border-t border-divider pt-2 text-[10px] leading-relaxed text-muted-foreground/85">{t('worlds.controls')}</p>}
          </div>
        )}

        {renderableBuild && document && terrain && isRenderableWorldSpec(buildSpec) ? (
          <WorldCanvas spec={buildSpec} terrain={terrain} instances={instances} selectedProtoId={null} artifacts={document.artifacts} backgroundColor={WORLD_VIEWER_BACKGROUND_COLOR} />
        ) : renderableScene ? (
          <ScenePlanCanvas plan={scenePlan} artifacts={document?.artifacts} backgroundColor={WORLD_VIEWER_BACKGROUND_COLOR} />
        ) : document ? (
          <div className="flex h-full flex-col items-center justify-center gap-3 px-6 text-center">
            <Sparkles className="size-7 text-primary/75" strokeWidth={1.5} aria-hidden="true" />
            <div>
              <p className="text-sm font-medium text-foreground">{zh ? '正在构建世界' : 'Building world'}</p>
              <p className="mt-1 text-xs text-muted-foreground">{stage ? `${stage.id} · ${stageLabel(stage.status, zh)}` : (zh ? '等待 Agent 写入 runtime spec' : 'Waiting for the Agent to write the runtime spec')}</p>
            </div>
            <p className="max-w-xs break-all font-mono text-[10px] text-muted-foreground/70">{document.id}</p>
          </div>
        ) : (
          <Viewer3D forceEmpty />
        )}
        {error && <div className="absolute bottom-3 right-3 max-w-[min(320px,calc(100%-24px))] rounded-md bg-destructive/10 px-2.5 py-1.5 text-[10px] text-destructive">{error}</div>}
      </div>
    </aside>
  )
}

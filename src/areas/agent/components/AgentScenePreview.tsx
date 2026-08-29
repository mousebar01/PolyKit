import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Box, LoaderCircle, RefreshCw, Sparkles } from 'lucide-react'

import { Badge } from '@shared/components/ui/badge'
import { Button } from '@shared/components/ui/button'
import { useAppStore } from '@shared/stores/appStore'
import { useI18n } from '@shared/i18n'
import WorldCanvas from '@areas/worlds/components/WorldCanvas'
import { buildTerrain, type BuiltTerrain } from '@areas/worlds/runtime/terrain'
import { solvePlacements } from '@areas/worlds/runtime/placement'
import { isRenderableWorldSpec } from '@areas/worlds/runtime/types'
import type { WorldDocument } from '@areas/worlds/types'
import { listWorlds, loadWorld, type WorldSummary } from '@areas/worlds/worldApi'

const REFRESH_INTERVAL_MS = 2500

function stageLabel(status: string | undefined, zh: boolean): string {
  if (status === 'done') return zh ? '已完成' : 'Done'
  if (status === 'running') return zh ? '进行中' : 'Running'
  if (status === 'blocked') return zh ? '受阻' : 'Blocked'
  return zh ? '待处理' : 'Pending'
}

function currentStage(document: WorldDocument | null): { id: string; status: string } | null {
  const stages = document?.agent_plan?.stages
  if (!stages || stages.length === 0) return null
  return stages.find((stage) => stage.status === 'running')
    ?? stages.find((stage) => stage.status !== 'done')
    ?? stages[stages.length - 1]
}

export default function AgentScenePreview(): JSX.Element {
  const { language, t } = useI18n()
  const zh = language === 'zh-CN'
  const apiUrl = useAppStore((state) => state.apiUrl)
  const [summary, setSummary] = useState<WorldSummary | null>(null)
  const [document, setDocument] = useState<WorldDocument | null>(null)
  const [terrain, setTerrain] = useState<BuiltTerrain | null>(null)
  const [instances, setInstances] = useState<WorldDocument['instances']>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
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
      if (isRenderableWorldSpec(nextDocument.spec)) {
        const nextTerrain = buildTerrain(nextDocument.spec, { resolution: 72 })
        setTerrain(nextTerrain)
        setInstances(nextDocument.instances.length > 0 ? nextDocument.instances : solvePlacements(nextDocument.spec, nextTerrain))
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

  const stage = useMemo(() => currentStage(document), [document])
  const renderable = document && terrain && isRenderableWorldSpec(document.spec)

  return (
    <aside className="flex min-h-0 w-[42%] min-w-[320px] max-w-[520px] shrink-0 flex-col overflow-hidden border-l border-divider bg-card/25" aria-label={zh ? '当前场景预览' : 'Current scene preview'}>
      <header className="flex shrink-0 items-center gap-2 border-b border-divider px-3 py-2.5">
        <Box className="size-4 text-primary" strokeWidth={1.8} aria-hidden="true" />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <h2 className="truncate text-xs font-semibold text-foreground">{zh ? '当前场景' : 'Current scene'}</h2>
            {document && <Badge variant={renderable ? 'default' : 'secondary'} className="h-5 px-1.5 text-[10px]">{renderable ? (zh ? '可预览' : 'Preview') : (zh ? '规划中' : 'Planning')}</Badge>}
          </div>
          <p className="truncate text-[10px] text-muted-foreground">{summary?.name ?? (zh ? '等待 Agent 规划场景' : 'Waiting for the Agent to plan a scene')}</p>
        </div>
        <Button type="button" variant="ghost" size="icon" className="size-7 text-muted-foreground" onClick={() => void refresh()} disabled={loading} title={zh ? '刷新场景' : 'Refresh scene'} aria-label={zh ? '刷新场景' : 'Refresh scene'}>
          {loading ? <LoaderCircle className="size-3.5 animate-spin" aria-hidden="true" /> : <RefreshCw className="size-3.5" aria-hidden="true" />}
        </Button>
      </header>

      <div className="relative min-h-0 flex-1 overflow-hidden bg-muted/35">
        {renderable ? (
          <>
            <WorldCanvas spec={document.spec} terrain={terrain} instances={instances} selectedProtoId={null} artifacts={document.artifacts} />
            <div className="pointer-events-none absolute left-3 top-3 max-w-[calc(100%-24px)] rounded-md bg-background/80 px-2.5 py-1.5 text-[11px] text-muted-foreground backdrop-blur-sm">
              <span className="font-medium text-foreground">{document.name}</span>
              <span className="mx-1.5 text-divider">·</span>
              <span className="font-mono">{document.id}</span>
            </div>
            <div className="pointer-events-none absolute bottom-3 left-3 rounded-md bg-background/75 px-2.5 py-1.5 text-[10px] text-muted-foreground backdrop-blur-sm">
              {t('worlds.controls')}
            </div>
          </>
        ) : document ? (
          <div className="flex h-full flex-col items-center justify-center gap-3 px-6 text-center">
            <Sparkles className="size-7 text-primary/75" strokeWidth={1.5} aria-hidden="true" />
            <div>
              <p className="text-sm font-medium text-foreground">{zh ? '正在规划场景' : 'Planning scene'}</p>
              <p className="mt-1 text-xs text-muted-foreground">{stage ? `${stage.id} · ${stageLabel(stage.status, zh)}` : (zh ? '等待 Agent 写入场景方案' : 'Waiting for the Agent to write the scene plan')}</p>
            </div>
            <p className="max-w-xs break-all font-mono text-[10px] text-muted-foreground/70">{document.id}</p>
          </div>
        ) : (
          <div className="flex h-full flex-col items-center justify-center gap-2 px-6 text-center text-muted-foreground">
            <Box className="size-7 opacity-60" strokeWidth={1.5} aria-hidden="true" />
            <p className="text-xs">{error ?? (zh ? 'Agent 创建场景后，这里会自动显示预览' : 'The preview will appear after the Agent creates a scene')}</p>
          </div>
        )}
        {error && document && <div className="absolute bottom-3 right-3 max-w-[min(320px,calc(100%-24px))] rounded-md bg-destructive/10 px-2.5 py-1.5 text-[10px] text-destructive">{error}</div>}
      </div>
    </aside>
  )
}

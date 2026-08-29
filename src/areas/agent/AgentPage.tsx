import { useCallback, useEffect, useRef, useState, type KeyboardEvent as ReactKeyboardEvent, type PointerEvent as ReactPointerEvent, type RefObject } from 'react'
import { Bot, ChevronLeft, ChevronRight, LoaderCircle, MessageSquare, Plus, RefreshCw, Settings2 } from 'lucide-react'

import { Button } from '@shared/components/ui'
import { useI18n } from '@shared/i18n'
import { useNavStore } from '@shared/stores/navStore'
import type { AgentSettings } from '@shared/types/runtime.d'
import { ChatWindow } from '@agent/components/ChatWindow'
import { I18nProvider } from '@agent/hooks/useI18n'
import '@agent/app/globals.css'
import AgentScenePreview from './components/AgentScenePreview'

interface AgentSessionRecord {
  path: string
  id: string
  cwd: string
  name?: string
  created: string
  modified: string
  messageCount: number
  firstMessage: string
  archived?: boolean
}

const ACTIVE_SESSION_STORAGE_KEY = 'polykit-agent:active-session-id'
const SESSION_HISTORY_COLLAPSED_STORAGE_KEY = 'polykit-agent:session-history-collapsed'
const PREVIEW_WIDTH_STORAGE_KEY = 'polykit-agent:scene-preview-width'
const DEFAULT_PREVIEW_WIDTH = 42
const MIN_PREVIEW_WIDTH_PX = 320
const MIN_DIALOGUE_WIDTH_PX = 420
const MIN_PREVIEW_WIDTH_PERCENT = 28
const MAX_PREVIEW_WIDTH_PERCENT = 62

function readPreviewWidth(): number {
  try {
    const value = Number(window.localStorage.getItem(PREVIEW_WIDTH_STORAGE_KEY))
    return Number.isFinite(value)
      ? Math.min(MAX_PREVIEW_WIDTH_PERCENT, Math.max(MIN_PREVIEW_WIDTH_PERCENT, value))
      : DEFAULT_PREVIEW_WIDTH
  } catch {
    return DEFAULT_PREVIEW_WIDTH
  }
}

function previewWidthBounds(containerWidth: number): { min: number; max: number } {
  const width = Math.max(containerWidth, 1)
  const min = Math.max(MIN_PREVIEW_WIDTH_PERCENT, (MIN_PREVIEW_WIDTH_PX / width) * 100)
  const max = Math.min(MAX_PREVIEW_WIDTH_PERCENT, Math.max(min, ((width - MIN_DIALOGUE_WIDTH_PX) / width) * 100))
  return { min, max }
}

function clampPreviewWidth(value: number, containerWidth: number): number {
  const { min, max } = previewWidthBounds(containerWidth)
  return Math.min(max, Math.max(min, value))
}

function readActiveSessionId(): string | null {
  try {
    return window.localStorage.getItem(ACTIVE_SESSION_STORAGE_KEY)
  } catch {
    return null
  }
}

function writeActiveSessionId(id: string | null): void {
  try {
    if (id) window.localStorage.setItem(ACTIVE_SESSION_STORAGE_KEY, id)
    else window.localStorage.removeItem(ACTIVE_SESSION_STORAGE_KEY)
  } catch {
    // Local storage can be unavailable in private browsing; server history remains the source of truth.
  }
}

function readSessionHistoryCollapsed(): boolean {
  try {
    return window.localStorage.getItem(SESSION_HISTORY_COLLAPSED_STORAGE_KEY) === 'true'
  } catch {
    return false
  }
}

function formatSessionDate(value: string, language: string): string {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return ''
  return date.toLocaleDateString(language === 'zh-CN' ? 'zh-CN' : 'en-US', {
    month: 'short',
    day: 'numeric',
  })
}

function normalizeSession(value: unknown): AgentSessionRecord | null {
  if (!value || typeof value !== 'object') return null
  const item = value as Partial<AgentSessionRecord>
  if (typeof item.id !== 'string' || typeof item.cwd !== 'string') return null
  return {
    path: typeof item.path === 'string' ? item.path : '',
    id: item.id,
    cwd: item.cwd,
    name: typeof item.name === 'string' ? item.name : undefined,
    created: typeof item.created === 'string' ? item.created : '',
    modified: typeof item.modified === 'string' ? item.modified : '',
    messageCount: typeof item.messageCount === 'number' ? item.messageCount : 0,
    firstMessage: typeof item.firstMessage === 'string' ? item.firstMessage : '',
    archived: item.archived === true,
  }
}

function sessionTitle(session: AgentSessionRecord, fallback: string): string {
  return session.name?.trim() || session.firstMessage?.trim() || fallback
}

function AgentSessionHistory({
  sessions,
  selectedSessionId,
  loading,
  error,
  language,
  onNewSession,
  onRefresh,
  onSelect,
  collapsed,
  onToggleCollapsed,
}: {
  sessions: AgentSessionRecord[]
  selectedSessionId: string | null
  loading: boolean
  error: boolean
  language: string
  onNewSession: () => void
  onRefresh: () => void
  onSelect: (session: AgentSessionRecord) => void
  collapsed: boolean
  onToggleCollapsed: () => void
}): JSX.Element {
  const { t } = useI18n()

  if (collapsed) {
    return (
      <aside className="flex w-10 shrink-0 flex-col items-center border-r border-divider bg-card/45" aria-label={t('agent.sessionHistory')}>
        <div className="flex w-full shrink-0 justify-center border-b border-divider py-2.5">
          <Button
            type="button"
            variant="ghost"
            size="icon"
            className="size-7 text-muted-foreground"
            onClick={onToggleCollapsed}
            title={t('agent.expandSessionHistory')}
            aria-label={t('agent.expandSessionHistory')}
            aria-expanded={false}
          >
            <ChevronRight className="size-3.5" aria-hidden="true" />
          </Button>
        </div>
        <div className="flex min-h-0 flex-1 flex-col items-center gap-2 py-2">
          <MessageSquare className="size-4 text-primary" strokeWidth={1.8} aria-hidden="true" />
          <span className="font-mono text-[10px] text-muted-foreground">{sessions.length}</span>
          <Button
            type="button"
            variant="ghost"
            size="icon"
            className="size-7 text-muted-foreground"
            onClick={onNewSession}
            title={t('agent.newSession')}
            aria-label={t('agent.newSession')}
          >
            <Plus className="size-3.5" aria-hidden="true" />
          </Button>
        </div>
      </aside>
    )
  }

  return (
    <aside className="flex w-[232px] shrink-0 flex-col border-r border-divider bg-card/45" aria-label={t('agent.sessionHistory')}>
      <div className="flex shrink-0 items-center gap-2 border-b border-divider px-3 py-2.5">
        <MessageSquare className="size-4 text-primary" strokeWidth={1.8} aria-hidden="true" />
        <div className="min-w-0 flex-1">
          <p className="truncate text-xs font-semibold text-foreground">{t('agent.sessionHistory')}</p>
          <p className="text-[10px] text-muted-foreground">{sessions.length}</p>
        </div>
        <Button
          type="button"
          variant="ghost"
          size="icon"
          className="size-7 shrink-0 text-muted-foreground"
          onClick={onToggleCollapsed}
          title={t('agent.collapseSessionHistory')}
          aria-label={t('agent.collapseSessionHistory')}
          aria-expanded
        >
          <ChevronLeft className="size-3.5" aria-hidden="true" />
        </Button>
        <Button
          type="button"
          variant="ghost"
          size="icon"
          className="size-7 shrink-0 text-muted-foreground"
          onClick={onRefresh}
          disabled={loading}
          title={t('agent.refreshSessions')}
          aria-label={t('agent.refreshSessions')}
        >
          <RefreshCw className={`size-3.5 ${loading ? 'animate-spin' : ''}`} aria-hidden="true" />
        </Button>
      </div>

      <div className="shrink-0 p-2">
        <Button type="button" size="sm" className="h-8 w-full justify-start gap-2" onClick={onNewSession}>
          <Plus className="size-3.5" aria-hidden="true" />
          {t('agent.newSession')}
        </Button>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-2 pb-2">
        {loading && sessions.length === 0 && (
          <div className="flex items-center justify-center gap-2 rounded-md border border-border/30 bg-muted/20 px-3 py-3 text-[11px] text-muted-foreground">
            <LoaderCircle className="size-3.5 animate-spin" aria-hidden="true" />
            {t('common.loading')}
          </div>
        )}

        {!loading && error && (
          <div className="rounded-md border border-destructive/25 bg-destructive/10 px-3 py-2.5 text-[11px] text-destructive">
            {t('agent.sessionLoadError')}
            <button type="button" className="ml-1 underline underline-offset-2" onClick={onRefresh}>
              {t('common.retry')}
            </button>
          </div>
        )}

        {!loading && !error && sessions.length === 0 && (
          <div className="rounded-md border border-dashed border-border/40 px-3 py-6 text-center text-[11px] text-muted-foreground">
            {t('agent.noSessions')}
          </div>
        )}

        <div className="flex flex-col gap-1">
          {sessions.map((session) => {
            const active = session.id === selectedSessionId
            return (
              <button
                key={session.id}
                type="button"
                onClick={() => onSelect(session)}
                className={`group flex w-full min-w-0 flex-col gap-1 rounded-md border px-2.5 py-2 text-left transition-colors ${active
                  ? 'border-primary/35 bg-primary/10 text-foreground'
                  : 'border-transparent text-muted-foreground hover:border-border/35 hover:bg-muted/55 hover:text-foreground'}`}
              >
                <span className="flex min-w-0 items-center gap-1.5">
                  <span className={`size-1.5 shrink-0 rounded-full ${active ? 'bg-primary' : 'bg-muted-foreground/45'}`} />
                  <span className="min-w-0 flex-1 truncate text-[11px] font-medium">
                    {sessionTitle(session, t('agent.newSession'))}
                  </span>
                </span>
                <span className="flex min-w-0 items-center gap-1.5 pl-3 text-[10px] text-muted-foreground/80">
                  <span className="truncate">{session.cwd.split(/[\\/]/).filter(Boolean).pop() || session.cwd}</span>
                  <span className="ml-auto shrink-0">{formatSessionDate(session.modified, language)}</span>
                </span>
                <span className="pl-3 text-[10px] text-muted-foreground/65">
                  {t('agent.messagesCount', { count: session.messageCount })}
                </span>
              </button>
            )
          })}
        </div>
      </div>
    </aside>
  )
}

function AgentSplitHandle({
  width,
  label,
  splitPaneRef,
  onWidthChange,
}: {
  width: number
  label: string
  splitPaneRef: RefObject<HTMLDivElement>
  onWidthChange: (width: number) => void
}): JSX.Element {
  const [resizing, setResizing] = useState(false)
  const dragRef = useRef<{ startX: number; startWidth: number; containerWidth: number } | null>(null)
  const previousBodyStyleRef = useRef<{ cursor: string; userSelect: string } | null>(null)

  const stopResize = useCallback(() => {
    dragRef.current = null
    setResizing(false)
    if (previousBodyStyleRef.current) {
      document.body.style.cursor = previousBodyStyleRef.current.cursor
      document.body.style.userSelect = previousBodyStyleRef.current.userSelect
      previousBodyStyleRef.current = null
    }
  }, [])

  useEffect(() => {
    const handleWindowBlur = () => stopResize()
    window.addEventListener('blur', handleWindowBlur)
    return () => {
      window.removeEventListener('blur', handleWindowBlur)
      stopResize()
    }
  }, [stopResize])

  const handlePointerDown = useCallback((event: ReactPointerEvent<HTMLDivElement>) => {
    if (event.button !== 0) return
    const container = splitPaneRef.current
    if (!container) return
    event.preventDefault()
    event.currentTarget.setPointerCapture(event.pointerId)
    dragRef.current = {
      startX: event.clientX,
      startWidth: width,
      containerWidth: container.getBoundingClientRect().width,
    }
    previousBodyStyleRef.current = {
      cursor: document.body.style.cursor,
      userSelect: document.body.style.userSelect,
    }
    document.body.style.cursor = 'col-resize'
    document.body.style.userSelect = 'none'
    setResizing(true)
  }, [splitPaneRef, width])

  const handlePointerMove = useCallback((event: ReactPointerEvent<HTMLDivElement>) => {
    const drag = dragRef.current
    if (!drag) return
    const deltaPercent = ((event.clientX - drag.startX) / Math.max(drag.containerWidth, 1)) * 100
    onWidthChange(clampPreviewWidth(drag.startWidth - deltaPercent, drag.containerWidth))
  }, [onWidthChange])

  const handleKeyDown = useCallback((event: ReactKeyboardEvent<HTMLDivElement>) => {
    const containerWidth = splitPaneRef.current?.getBoundingClientRect().width ?? 1200
    const { min, max } = previewWidthBounds(containerWidth)
    const step = 3
    let next: number | null = null
    if (event.key === 'ArrowLeft') next = Math.min(max, width + step)
    if (event.key === 'ArrowRight') next = Math.max(min, width - step)
    if (event.key === 'Home') next = min
    if (event.key === 'End') next = max
    if (next === null) return
    event.preventDefault()
    onWidthChange(next)
  }, [onWidthChange, splitPaneRef, width])

  const handleDoubleClick = useCallback(() => {
    const containerWidth = splitPaneRef.current?.getBoundingClientRect().width ?? 1200
    onWidthChange(clampPreviewWidth(DEFAULT_PREVIEW_WIDTH, containerWidth))
  }, [onWidthChange, splitPaneRef])

  return (
    <div
      role="separator"
      aria-orientation="vertical"
      aria-label={label}
      aria-valuemin={Math.round(previewWidthBounds(splitPaneRef.current?.getBoundingClientRect().width ?? 1200).min)}
      aria-valuemax={Math.round(previewWidthBounds(splitPaneRef.current?.getBoundingClientRect().width ?? 1200).max)}
      aria-valuenow={Math.round(width)}
      tabIndex={0}
      className={`group relative z-20 w-px shrink-0 cursor-col-resize bg-divider outline-none transition-colors focus-visible:bg-primary ${resizing ? 'bg-primary' : ''}`}
      onPointerDown={handlePointerDown}
      onPointerMove={handlePointerMove}
      onPointerUp={stopResize}
      onPointerCancel={stopResize}
      onDoubleClick={handleDoubleClick}
      onKeyDown={handleKeyDown}
    >
      <span className="absolute inset-y-0 -left-2 -right-2" aria-hidden="true" />
      <span className={`pointer-events-none absolute left-1/2 top-1/2 h-10 w-px -translate-x-1/2 -translate-y-1/2 rounded-full bg-primary/70 transition-opacity ${resizing ? 'opacity-100' : 'opacity-0 group-hover:opacity-100'}`} aria-hidden="true" />
    </div>
  )
}

export default function AgentPage(): JSX.Element {
  const { language, t } = useI18n()
  const openSettings = useNavStore((state) => state.openSettings)
  const [workspaceDir, setWorkspaceDir] = useState<string | null>(null)
  const [agentSettings, setAgentSettings] = useState<AgentSettings | null>(null)
  const [sessions, setSessions] = useState<AgentSessionRecord[]>([])
  const [selectedSession, setSelectedSession] = useState<AgentSessionRecord | null>(null)
  const [sessionLoading, setSessionLoading] = useState(false)
  const [sessionError, setSessionError] = useState(false)
  const [sessionKey, setSessionKey] = useState(0)
  const [sessionHistoryCollapsed, setSessionHistoryCollapsed] = useState(readSessionHistoryCollapsed)
  const [previewWidth, setPreviewWidth] = useState(readPreviewWidth)
  const splitPaneRef = useRef<HTMLDivElement>(null)
  const initialSelectionDone = useRef(false)

  const updatePreviewWidth = useCallback((next: number) => {
    setPreviewWidth((current) => Math.abs(current - next) < 0.1 ? current : next)
  }, [])

  const toggleSessionHistoryCollapsed = useCallback(() => {
    setSessionHistoryCollapsed((current) => !current)
  }, [])

  useEffect(() => {
    try {
      window.localStorage.setItem(SESSION_HISTORY_COLLAPSED_STORAGE_KEY, String(sessionHistoryCollapsed))
    } catch {
      // Local storage is optional; the current layout remains usable without it.
    }
  }, [sessionHistoryCollapsed])

  useEffect(() => {
    try {
      window.localStorage.setItem(PREVIEW_WIDTH_STORAGE_KEY, String(previewWidth))
    } catch {
      // Local storage is optional; the current layout remains usable without it.
    }
  }, [previewWidth])

  useEffect(() => {
    const node = splitPaneRef.current
    if (!node || typeof ResizeObserver === 'undefined') return
    const observer = new ResizeObserver((entries) => {
      const containerWidth = entries[0]?.contentRect.width
      if (!containerWidth) return
      setPreviewWidth((current) => clampPreviewWidth(current, containerWidth))
    })
    observer.observe(node)
    return () => observer.disconnect()
  }, [])

  useEffect(() => {
    let cancelled = false
    window.polykit.settings.get()
      .then((settings) => {
        if (!cancelled) setWorkspaceDir(settings.workspaceDir || null)
        if (!cancelled) setAgentSettings(settings.agent)
      })
      .catch(() => {
        // Keep the new-session draft disabled until the server provides a safe workspace root.
      })
    return () => { cancelled = true }
  }, [])

  const loadSessions = useCallback(async (): Promise<AgentSessionRecord[]> => {
    setSessionLoading(true)
    try {
      const response = await fetch('/agent/sessions', { cache: 'no-store' })
      const body = await response.json().catch(() => ({})) as { sessions?: unknown[]; error?: string }
      if (!response.ok) throw new Error(body.error ?? `HTTP ${response.status}`)
      const next = (body.sessions ?? [])
        .map(normalizeSession)
        .filter((session): session is AgentSessionRecord => session !== null && !session.archived)
        .sort((a, b) => b.modified.localeCompare(a.modified))
      setSessions(next)
      setSessionError(false)

      if (!initialSelectionDone.current) {
        initialSelectionDone.current = true
        const storedId = readActiveSessionId()
        const initial = next.find((session) => session.id === storedId) ?? next[0]
        if (initial) {
          setSelectedSession(initial)
          writeActiveSessionId(initial.id)
          setSessionKey((key) => key + 1)
        } else {
          writeActiveSessionId(null)
        }
      } else {
        setSelectedSession((current) => {
          if (!current) return current
          return next.find((session) => session.id === current.id) ?? current
        })
      }
      return next
    } catch {
      setSessionError(true)
      return []
    } finally {
      setSessionLoading(false)
    }
  }, [])

  useEffect(() => {
    if (agentSettings?.enabled === false || !workspaceDir) return
    void loadSessions()
  }, [agentSettings?.enabled, loadSessions, workspaceDir])

  const handleSelectSession = useCallback((session: AgentSessionRecord) => {
    initialSelectionDone.current = true
    setSelectedSession(session)
    writeActiveSessionId(session.id)
    setSessionKey((key) => key + 1)
  }, [])

  const handleNewSession = useCallback(() => {
    if (!workspaceDir) return
    initialSelectionDone.current = true
    setSelectedSession(null)
    writeActiveSessionId(null)
    setSessionKey((key) => key + 1)
  }, [workspaceDir])

  const handleSessionCreated = useCallback((value: unknown) => {
    const created = normalizeSession(value)
    if (!created) {
      void loadSessions()
      return
    }
    initialSelectionDone.current = true
    setSelectedSession(created)
    writeActiveSessionId(created.id)
    void loadSessions()
  }, [loadSessions])

  const handleSessionForked = useCallback((sessionId: string) => {
    void loadSessions().then((next) => {
      const created = next.find((session) => session.id === sessionId)
      if (!created) return
      initialSelectionDone.current = true
      setSelectedSession(created)
      writeActiveSessionId(created.id)
      setSessionKey((key) => key + 1)
    })
  }, [loadSessions])

  const toolPreset = agentSettings?.toolProfile === 'safe'
    ? 'none'
    : agentSettings?.toolProfile === 'developer' ? 'full' : 'default'
  const initialModel = agentSettings?.defaultProvider && agentSettings.defaultModel
    ? { provider: agentSettings.defaultProvider, modelId: agentSettings.defaultModel }
    : null

  return (
    <div className="agent-chat-root flex h-full min-h-0 flex-col overflow-hidden rounded-lg border border-divider bg-card/20">
      <header className="flex h-10 shrink-0 items-center justify-between bg-card/65 px-3">
        <div className="flex items-center gap-2.5">
          <Bot className="size-4 text-primary" strokeWidth={1.8} />
          <h1 className="text-sm font-semibold text-foreground">{t('agent.title')}</h1>
        </div>
        <Button type="button" variant="ghost" size="sm" onClick={() => openSettings('agent')}>
          <Settings2 className="mr-1.5 size-3.5" />
          {t('agent.openSettings')}
        </Button>
      </header>
      <div className="flex min-h-0 flex-1 overflow-hidden">
        {agentSettings?.enabled === false ? (
          <div className="flex h-full flex-1 items-center justify-center px-6 text-sm text-muted-foreground">
            <div className="max-w-md text-center">
              <p>{t('agent.disabled')}</p>
              <Button type="button" variant="outline" size="sm" className="mt-3" onClick={() => openSettings('agent')}>
                {t('agent.openSettings')}
              </Button>
            </div>
          </div>
        ) : agentSettings && workspaceDir ? (
          <I18nProvider>
            <AgentSessionHistory
              sessions={sessions}
              selectedSessionId={selectedSession?.id ?? null}
              loading={sessionLoading}
              error={sessionError}
              language={language}
              onNewSession={handleNewSession}
              onRefresh={() => { void loadSessions() }}
              onSelect={handleSelectSession}
              collapsed={sessionHistoryCollapsed}
              onToggleCollapsed={toggleSessionHistoryCollapsed}
            />
            <div ref={splitPaneRef} className="flex min-h-0 min-w-0 flex-1 overflow-hidden">
              <div className="min-h-0 min-w-0 flex-1 overflow-hidden">
                <ChatWindow
                  key={sessionKey}
                  session={selectedSession}
                  newSessionCwd={selectedSession ? null : workspaceDir}
                  showWorkspacePicker={false}
                  initialModel={initialModel}
                  initialToolPreset={toolPreset}
                  initialThinkingLevel={agentSettings.thinkingLevel}
                  onSessionCreated={handleSessionCreated}
                  onSessionForked={handleSessionForked}
                  onAgentEnd={() => { void loadSessions() }}
                />
              </div>
              <AgentSplitHandle
                width={previewWidth}
                label={t('agent.resizePreview')}
                splitPaneRef={splitPaneRef}
                onWidthChange={updatePreviewWidth}
              />
              <AgentScenePreview width={previewWidth} />
            </div>
          </I18nProvider>
        ) : (
          <div className="flex h-full flex-1 items-center justify-center text-sm text-muted-foreground">
            {t('settings.pleaseWait')}
          </div>
        )}
      </div>
    </div>
  )
}

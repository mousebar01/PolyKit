import { useCallback, useEffect, useRef, useState } from 'react'
import { Bot, LoaderCircle, MessageSquare, Plus, RefreshCw, Settings2 } from 'lucide-react'

import { Button } from '@shared/components/ui'
import { useI18n } from '@shared/i18n'
import { useNavStore } from '@shared/stores/navStore'
import type { AgentSettings } from '@shared/types/runtime.d'
import { ChatWindow } from '@agent/components/ChatWindow'
import { I18nProvider } from '@agent/hooks/useI18n'
import '@agent/app/globals.css'

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
}: {
  sessions: AgentSessionRecord[]
  selectedSessionId: string | null
  loading: boolean
  error: boolean
  language: string
  onNewSession: () => void
  onRefresh: () => void
  onSelect: (session: AgentSessionRecord) => void
}): JSX.Element {
  const { t } = useI18n()

  return (
    <aside className="flex w-[232px] shrink-0 flex-col border-r border-divider bg-card/45">
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
  const initialSelectionDone = useRef(false)

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
    <div className="agent-chat-root flex h-full min-h-0 flex-col bg-background">
      <header className="flex h-10 shrink-0 items-center justify-between border-b border-divider bg-card/65 px-3">
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
            />
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

import { useEffect, useState } from 'react'
import { Bot, Settings2 } from 'lucide-react'

import { Button } from '@shared/components/ui'
import { useI18n } from '@shared/i18n'
import { useNavStore } from '@shared/stores/navStore'
import type { AgentSettings } from '@shared/types/runtime.d'
import { ChatWindow } from '@agent/components/ChatWindow'
import { I18nProvider } from '@agent/hooks/useI18n'
import '@agent/app/globals.css'

export default function AgentPage(): JSX.Element {
  const { t } = useI18n()
  const openSettings = useNavStore((state) => state.openSettings)
  const [workspaceDir, setWorkspaceDir] = useState<string | null>(null)
  const [agentSettings, setAgentSettings] = useState<AgentSettings | null>(null)

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

  const toolPreset = agentSettings?.toolProfile === 'safe'
    ? 'none'
    : agentSettings?.toolProfile === 'developer' ? 'full' : 'default'
  const initialModel = agentSettings?.defaultProvider && agentSettings.defaultModel
    ? { provider: agentSettings.defaultProvider, modelId: agentSettings.defaultModel }
    : null

  return (
    <div className="agent-chat-root flex h-full min-h-0 flex-col bg-background">
      <header className="flex h-10 shrink-0 items-center justify-between border-b border-border bg-card px-3">
        <div className="flex items-center gap-2.5">
          <Bot className="size-4 text-primary" strokeWidth={1.8} />
          <h1 className="text-sm font-semibold text-foreground">{t('agent.title')}</h1>
        </div>
        <Button type="button" variant="ghost" size="sm" onClick={() => openSettings('agent')}>
          <Settings2 className="mr-1.5 size-3.5" />
          {t('agent.openSettings')}
        </Button>
      </header>
      <div className="min-h-0 flex-1 overflow-hidden">
        {agentSettings?.enabled === false ? (
          <div className="flex h-full items-center justify-center px-6 text-sm text-muted-foreground">
            <div className="max-w-md text-center">
              <p>{t('agent.disabled')}</p>
              <Button type="button" variant="outline" size="sm" className="mt-3" onClick={() => openSettings('agent')}>
                {t('agent.openSettings')}
              </Button>
            </div>
          </div>
        ) : agentSettings && workspaceDir ? (
          <I18nProvider>
            <ChatWindow
              session={null}
              newSessionCwd={workspaceDir}
              showWorkspacePicker={false}
              initialModel={initialModel}
              initialToolPreset={toolPreset}
              initialThinkingLevel={agentSettings.thinkingLevel}
            />
          </I18nProvider>
        ) : (
          <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
            {t('settings.pleaseWait')}
          </div>
        )}
      </div>
    </div>
  )
}

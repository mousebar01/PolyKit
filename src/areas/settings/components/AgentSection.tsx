import { useEffect, useState } from 'react'
import { Archive, Bot, Check, Layers3, Loader2, Plug, Settings2, Sparkles } from 'lucide-react'

import type {
  AgentSettings,
  AgentSettingsPatch,
} from '@shared/types/runtime.d'
import { Badge, Button, Switch } from '@shared/components/ui'
import { useI18n } from '@shared/i18n'
import { SettingsCard, SettingsPathRow, SettingsRow, SettingsSection } from './SettingsLayout'
import { I18nProvider } from '@agent/hooks/useI18n'
import { ArchivedSessionsConfig } from '@agent/components/ArchivedSessionsConfig'
import { ModelsConfig } from '@agent/components/ModelsConfig'
import { SkillsConfig } from '@agent/components/SkillsConfig'
import { PluginsConfig } from '@agent/components/PluginsConfig'
import { McpConfig } from '@agent/components/McpConfig'
import '@agent/app/globals.css'

type SaveState = 'idle' | 'saving' | 'saved' | 'error'
type AgentSubsection = 'runtime' | 'archives' | 'models' | 'skills' | 'plugins' | 'mcp'

const DEFAULT_AGENT: AgentSettings = {
  enabled: true,
  defaultProvider: '',
  defaultModel: '',
  thinkingLevel: 'medium',
  toolProfile: 'blender',
  sessionDir: '',
}

export function AgentSection(): JSX.Element {
  const { t } = useI18n()
  const [form, setForm] = useState<AgentSettings>(DEFAULT_AGENT)
  const [saveState, setSaveState] = useState<SaveState>('idle')
  const [subsection, setSubsection] = useState<AgentSubsection>('runtime')
  const [workspaceDir, setWorkspaceDir] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    window.polykit.settings.get()
      .then((settings) => {
        if (!cancelled) setForm({ ...DEFAULT_AGENT, ...(settings.agent ?? {}) })
        if (!cancelled) setWorkspaceDir(settings.workspaceDir || null)
      })
      .catch(() => {
        // Keep the safe defaults when an older runtime has no Agent settings.
      })
    return () => { cancelled = true }
  }, [])

  function update(patch: AgentSettingsPatch): void {
    setForm((current) => ({ ...current, ...patch }))
    setSaveState('idle')
  }

  const subSections: { id: AgentSubsection; label: string; icon: typeof Bot }[] = [
    { id: 'runtime', label: t('settings.agentRuntime'), icon: Settings2 },
    { id: 'models', label: t('settings.agentModels'), icon: Layers3 },
    { id: 'skills', label: t('settings.agentSkills'), icon: Sparkles },
    { id: 'plugins', label: t('settings.agentPlugins'), icon: Bot },
    { id: 'mcp', label: t('settings.agentMcp'), icon: Plug },
    { id: 'archives', label: t('settings.agentArchives'), icon: Archive },
  ]

  function renderAdvancedPanel(): JSX.Element | null {
    if (subsection === 'runtime') return null
    return (
      <div className={`agent-chat-root flex flex-col overflow-hidden rounded-lg border border-divider bg-card ${subsection === 'models' ? '' : 'min-h-[460px]'}`}>
        <I18nProvider>
          {subsection === 'models' && <ModelsConfig embedded onClose={() => setSubsection('runtime')} />}
          {subsection === 'skills' && (workspaceDir
            ? <SkillsConfig cwd={workspaceDir} embedded onClose={() => setSubsection('runtime')} />
            : <div className="flex min-h-[460px] items-center justify-center px-4 py-8 text-sm text-muted-foreground">{t('settings.pleaseWait')}</div>)}
          {subsection === 'plugins' && (workspaceDir
            ? <PluginsConfig cwd={workspaceDir} sessionId={null} embedded onClose={() => setSubsection('runtime')} onReloaded={() => undefined} />
            : <div className="flex min-h-[460px] items-center justify-center px-4 py-8 text-sm text-muted-foreground">{t('settings.pleaseWait')}</div>)}
          {subsection === 'mcp' && (workspaceDir
            ? <McpConfig cwd={workspaceDir} sessionId={null} onAgentConfigure={() => setSubsection('runtime')} onReloaded={() => undefined} />
            : <div className="flex min-h-[460px] items-center justify-center px-4 py-8 text-sm text-muted-foreground">{t('settings.pleaseWait')}</div>)}
          {subsection === 'archives' && <ArchivedSessionsConfig />}
        </I18nProvider>
      </div>
    )
  }

  async function handleSave(): Promise<void> {
    setSaveState('saving')
    try {
      const saved = await window.polykit.settings.set({
        agent: {
          enabled: form.enabled,
        },
      })
      setForm({ ...DEFAULT_AGENT, ...(saved.agent ?? form) })
      setSaveState('saved')
      window.setTimeout(() => setSaveState('idle'), 2500)
    } catch {
      setSaveState('error')
      window.setTimeout(() => setSaveState('idle'), 3500)
    }
  }

  return (
    <SettingsSection title={t('settings.agent')} subtitle={t('settings.agentSubtitle')}>
      <div className="mb-5 flex flex-wrap items-center gap-1 border-b border-divider pb-1">
        {subSections.map((item) => {
          const Icon = item.icon
          return (
            <Button
              key={item.id}
              type="button"
              variant="ghost"
              size="sm"
              aria-current={subsection === item.id ? 'page' : undefined}
              className={subsection === item.id
                ? 'h-8 gap-1.5 rounded-none border-b-2 border-primary bg-transparent px-3 text-primary hover:bg-transparent hover:text-primary'
                : 'h-8 gap-1.5 rounded-md px-3 text-muted-foreground hover:bg-muted/60 hover:text-foreground'}
              onClick={() => setSubsection(item.id)}
            >
              <Icon className="size-3.5" />
              {item.label}
            </Button>
          )
        })}
      </div>
      {subsection !== 'runtime' ? renderAdvancedPanel() : <div className="grid gap-4">
        <SettingsCard title={t('settings.agentRuntime')} description={t('settings.agentRuntimeDescription')}>
          <SettingsRow label={t('settings.agentEnabled')} description={t('settings.agentEnabledDescription')}>
            <Switch
              checked={form.enabled}
              onCheckedChange={(value) => update({ enabled: value })}
              aria-label={t('settings.agentEnabled')}
            />
          </SettingsRow>
          <SettingsRow label={t('settings.agentStatus')}>
            <Badge variant="outline" className="gap-1.5 border-emerald-500/30 bg-emerald-500/10 text-emerald-300">
              <Bot className="size-3.5" />
              {t('settings.agentIntegrated')}
            </Badge>
          </SettingsRow>
          <SettingsPathRow
            label={t('settings.agentSessionDirectory')}
            description={t('settings.agentSessionDirectoryDescription')}
            value={form.sessionDir || t('settings.agentSessionDirectoryPending')}
          />
          <div className="flex items-center justify-end px-5 py-4">
            <Button type="button" size="sm" onClick={() => { void handleSave() }} disabled={saveState === 'saving'}>
              {saveState === 'saving' ? <><Loader2 className="mr-1.5 size-3.5 animate-spin" />{t('settings.saving')}</> : null}
              {saveState === 'saved' ? <><Check className="mr-1.5 size-3.5" />{t('settings.saved')}</> : null}
              {saveState === 'error' ? t('settings.failed') : null}
              {saveState === 'idle' ? t('common.save') : null}
            </Button>
          </div>
        </SettingsCard>
      </div>}
    </SettingsSection>
  )
}

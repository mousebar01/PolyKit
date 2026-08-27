import { useEffect, useState } from 'react'
import { Archive, Bot, Check, EyeOff, Layers3, Loader2, Plug, Settings2, Sparkles } from 'lucide-react'

import type {
  AgentSettings,
  AgentSettingsPatch,
  AgentThinkingLevel,
  AgentToolProfile,
} from '@shared/types/runtime.d'
import { Badge, Button, Input, Select, SelectContent, SelectItem, SelectTrigger, SelectValue, Switch } from '@shared/components/ui'
import { useI18n } from '@shared/i18n'
import { SettingsCard, SettingsPathRow, SettingsRow, SettingsSection } from './SettingsLayout'
import { I18nProvider } from '@agent/hooks/useI18n'
import { ArchivedSessionsConfig } from '@agent/components/ArchivedSessionsConfig'
import { HiddenWorkspacesConfig } from '@agent/components/HiddenWorkspacesConfig'
import { ModelsConfig } from '@agent/components/ModelsConfig'
import { SkillsConfig } from '@agent/components/SkillsConfig'
import { PluginsConfig } from '@agent/components/PluginsConfig'
import { McpConfig } from '@agent/components/McpConfig'
import '@agent/app/globals.css'

type SaveState = 'idle' | 'saving' | 'saved' | 'error'
type AgentSubsection = 'runtime' | 'archives' | 'workspaces' | 'models' | 'skills' | 'plugins' | 'mcp'

const DEFAULT_AGENT: AgentSettings = {
  enabled: true,
  defaultProvider: '',
  defaultModel: '',
  thinkingLevel: 'medium',
  toolProfile: 'blender',
  sessionDir: '',
}

const THINKING_LEVELS: AgentThinkingLevel[] = ['off', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max']
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
    { id: 'workspaces', label: t('settings.agentWorkspaces'), icon: EyeOff },
  ]

  function renderAdvancedPanel(): JSX.Element | null {
    if (subsection === 'runtime') return null
    return (
      <I18nProvider>
        {subsection === 'models' && <ModelsConfig embedded onClose={() => setSubsection('runtime')} />}
        {subsection === 'skills' && (workspaceDir
          ? <SkillsConfig cwd={workspaceDir} embedded onClose={() => setSubsection('runtime')} />
          : <div className="rounded-md border border-border px-4 py-8 text-center text-sm text-muted-foreground">{t('settings.pleaseWait')}</div>)}
        {subsection === 'plugins' && (workspaceDir
          ? <PluginsConfig cwd={workspaceDir} sessionId={null} embedded onClose={() => setSubsection('runtime')} onReloaded={() => undefined} />
          : <div className="rounded-md border border-border px-4 py-8 text-center text-sm text-muted-foreground">{t('settings.pleaseWait')}</div>)}
        {subsection === 'mcp' && (workspaceDir
          ? <McpConfig cwd={workspaceDir} sessionId={null} onAgentConfigure={() => setSubsection('runtime')} onReloaded={() => undefined} />
          : <div className="rounded-md border border-border px-4 py-8 text-center text-sm text-muted-foreground">{t('settings.pleaseWait')}</div>)}
        {subsection === 'archives' && <ArchivedSessionsConfig />}
        {subsection === 'workspaces' && <HiddenWorkspacesConfig />}
      </I18nProvider>
    )
  }

  async function handleSave(): Promise<void> {
    setSaveState('saving')
    try {
      const saved = await window.polykit.settings.set({
        agent: {
          enabled: form.enabled,
          defaultProvider: form.defaultProvider.trim(),
          defaultModel: form.defaultModel.trim(),
          thinkingLevel: form.thinkingLevel,
          toolProfile: form.toolProfile,
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
      <div className="mb-4 flex flex-wrap gap-1 rounded-lg border border-border bg-card/60 p-1">
        {subSections.map((item) => {
          const Icon = item.icon
          return (
            <Button
              key={item.id}
              type="button"
              variant={subsection === item.id ? 'secondary' : 'ghost'}
              size="sm"
              className="gap-1.5"
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
        </SettingsCard>

        <SettingsCard title={t('settings.agentDefaults')} description={t('settings.agentDefaultsDescription')}>
          <SettingsRow label={t('settings.agentProvider')} description={t('settings.agentProviderHint')}>
            <Input
              value={form.defaultProvider}
              onChange={(event) => update({ defaultProvider: event.target.value })}
              placeholder="anthropic"
              autoComplete="off"
              spellCheck={false}
              aria-label={t('settings.agentProvider')}
              className="h-8 w-full font-mono text-xs"
            />
          </SettingsRow>
          <SettingsRow label={t('settings.agentModel')} description={t('settings.agentModelHint')}>
            <Input
              value={form.defaultModel}
              onChange={(event) => update({ defaultModel: event.target.value })}
              placeholder="claude-sonnet-4-5"
              autoComplete="off"
              spellCheck={false}
              aria-label={t('settings.agentModel')}
              className="h-8 w-full font-mono text-xs"
            />
          </SettingsRow>
          <SettingsRow label={t('settings.agentThinkingLevel')} description={t('settings.agentThinkingLevelHint')}>
            <Select value={form.thinkingLevel} onValueChange={(value) => update({ thinkingLevel: value as AgentThinkingLevel })}>
              <SelectTrigger className="w-full text-xs" aria-label={t('settings.agentThinkingLevel')}>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {THINKING_LEVELS.map((level) => (
                  <SelectItem key={level} value={level}>{level}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </SettingsRow>
          <SettingsRow label={t('settings.agentToolProfile')} description={t('settings.agentToolProfileHint')}>
            <Select value={form.toolProfile} onValueChange={(value) => update({ toolProfile: value as AgentToolProfile })}>
              <SelectTrigger className="w-full text-xs" aria-label={t('settings.agentToolProfile')}>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="safe">{t('settings.agentToolSafe')}</SelectItem>
                <SelectItem value="blender">{t('settings.agentToolBlender')}</SelectItem>
                <SelectItem value="developer">{t('settings.agentToolDeveloper')}</SelectItem>
              </SelectContent>
            </Select>
          </SettingsRow>
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

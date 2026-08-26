import { useEffect, useState } from 'react'
import { Eye, EyeOff, Loader2 } from 'lucide-react'

import { Button, Input, Select, SelectContent, SelectItem, SelectTrigger, SelectValue, Switch } from '@shared/components/ui'
import { useI18n } from '@shared/i18n'
import { SettingsCard, SettingsRow, SettingsSection } from './SettingsLayout'

interface ProxyForm {
  enabled: boolean
  url: string
  username: string
  password: string
  bypass: string
}

interface SourceForm {
  huggingfaceEndpoint: string
  pypiIndexUrl: string
  pytorchIndexUrl: string
}

type SourceKind = keyof SourceForm
type SourcePresetId = 'official' | 'tuna' | 'aliyun' | 'ustc' | 'custom'

type SourcePreset = {
  id: SourcePresetId
  labelKey:
    | 'settings.sourcePresetOfficial'
    | 'settings.sourcePresetTuna'
    | 'settings.sourcePresetAliyun'
    | 'settings.sourcePresetUstc'
    | 'settings.sourcePresetCustom'
  values: SourceForm
}

const EMPTY_FORM: ProxyForm = { enabled: false, url: '', username: '', password: '', bypass: '' }
const EMPTY_SOURCES: SourceForm = { huggingfaceEndpoint: '', pypiIndexUrl: '', pytorchIndexUrl: '' }

// PyTorch's CUDA wheels stay on the official index by default. The common
// China presets only replace Hugging Face and PyPI, while the editable field
// below still allows a site-specific PyTorch mirror.
const SOURCE_PRESETS: readonly SourcePreset[] = [
  { id: 'official', labelKey: 'settings.sourcePresetOfficial', values: EMPTY_SOURCES },
  {
    id: 'tuna',
    labelKey: 'settings.sourcePresetTuna',
    values: {
      huggingfaceEndpoint: 'https://hf-mirror.com',
      pypiIndexUrl: 'https://pypi.tuna.tsinghua.edu.cn/simple',
      pytorchIndexUrl: '',
    },
  },
  {
    id: 'aliyun',
    labelKey: 'settings.sourcePresetAliyun',
    values: {
      huggingfaceEndpoint: 'https://hf-mirror.com',
      pypiIndexUrl: 'https://mirrors.aliyun.com/pypi/simple',
      pytorchIndexUrl: '',
    },
  },
  {
    id: 'ustc',
    labelKey: 'settings.sourcePresetUstc',
    values: {
      huggingfaceEndpoint: 'https://hf-mirror.com',
      pypiIndexUrl: 'https://mirrors.ustc.edu.cn/pypi/simple',
      pytorchIndexUrl: '',
    },
  },
  { id: 'custom', labelKey: 'settings.sourcePresetCustom', values: EMPTY_SOURCES },
]

function sourcePresetFor(value: SourceForm): SourcePresetId {
  const match = SOURCE_PRESETS.find(({ id, values }) => id !== 'custom' && (
    values.huggingfaceEndpoint === value.huggingfaceEndpoint
      && values.pypiIndexUrl === value.pypiIndexUrl
      && values.pytorchIndexUrl === value.pytorchIndexUrl
  ))
  return match?.id ?? 'custom'
}

function isValidProxyUrl(value: string): boolean {
  try {
    const parsed = new URL(value)
    return ['http:', 'https:', 'socks5:', 'socks5h:'].includes(parsed.protocol)
      && Boolean(parsed.hostname)
      && parsed.port !== ''
  } catch {
    return false
  }
}

function isValidSourceUrl(value: string): boolean {
  if (!value.trim()) return true
  try {
    const parsed = new URL(value)
    return ['http:', 'https:'].includes(parsed.protocol) && Boolean(parsed.hostname)
  } catch {
    return false
  }
}

export function NetworkSection(): JSX.Element {
  const { t } = useI18n()
  const [form, setForm] = useState<ProxyForm>(EMPTY_FORM)
  const [saveStatus, setSaveStatus] = useState<'idle' | 'saving' | 'saved' | 'error'>('idle')
  const [testStatus, setTestStatus] = useState<'idle' | 'testing' | 'ok' | 'error' | 'invalid'>('idle')
  const [testError, setTestError] = useState<string | null>(null)
  const [passwordVisible, setPasswordVisible] = useState(false)
  const [sources, setSources] = useState<SourceForm>(EMPTY_SOURCES)
  const [selectedPreset, setSelectedPreset] = useState<SourcePresetId>('official')
  const [sourceSaveStatus, setSourceSaveStatus] = useState<'idle' | 'saving' | 'saved' | 'error'>('idle')
  const [sourceTestKind, setSourceTestKind] = useState<SourceKind | null>(null)
  const [sourceTestStatus, setSourceTestStatus] = useState<'idle' | 'ok' | 'error'>('idle')
  const [sourceTestError, setSourceTestError] = useState<string | null>(null)

  useEffect(() => {
    window.polykit.settings.get().then((settings) => {
      const proxy = settings.proxy
      setForm(proxy
        ? {
            enabled: proxy.enabled,
            url: proxy.url ?? '',
            username: proxy.username ?? '',
            password: proxy.password ?? '',
            bypass: proxy.bypass ?? '',
          }
        : EMPTY_FORM)
      const configuredSources = settings.sources
      setSources(configuredSources
        ? {
            huggingfaceEndpoint: configuredSources.huggingfaceEndpoint ?? '',
            pypiIndexUrl: configuredSources.pypiIndexUrl ?? '',
            pytorchIndexUrl: configuredSources.pytorchIndexUrl ?? '',
          }
        : EMPTY_SOURCES)
      setSelectedPreset(sourcePresetFor(configuredSources
        ? {
            huggingfaceEndpoint: configuredSources.huggingfaceEndpoint ?? '',
            pypiIndexUrl: configuredSources.pypiIndexUrl ?? '',
            pytorchIndexUrl: configuredSources.pytorchIndexUrl ?? '',
          }
        : EMPTY_SOURCES))
    })
  }, [])

  // Clear a finished test result after a moment so the button returns to "Test".
  useEffect(() => {
    if (testStatus === 'ok' || testStatus === 'error' || testStatus === 'invalid') {
      const timer = setTimeout(() => setTestStatus('idle'), 4000)
      return () => clearTimeout(timer)
    }
  }, [testStatus])

  function update(patch: Partial<ProxyForm>): void {
    setForm((current) => ({ ...current, ...patch }))
    setSaveStatus('idle')
    setTestError(null)
    if (testStatus !== 'idle') setTestStatus('idle')
  }

  function proxyPayload(): ProxyForm {
    return {
      enabled: form.enabled,
      url: form.url.trim(),
      username: form.username.trim(),
      password: form.password,
      bypass: form.bypass.trim(),
    }
  }

  function sourcePayload(): SourceForm {
    return {
      huggingfaceEndpoint: sources.huggingfaceEndpoint.trim(),
      pypiIndexUrl: sources.pypiIndexUrl.trim(),
      pytorchIndexUrl: sources.pytorchIndexUrl.trim(),
    }
  }

  function updateSource(kind: SourceKind, value: string): void {
    setSources((current) => ({ ...current, [kind]: value }))
    setSelectedPreset('custom')
    setSourceSaveStatus('idle')
    setSourceTestStatus('idle')
    setSourceTestError(null)
  }

  function applySourcePreset(presetId: SourcePresetId): void {
    setSelectedPreset(presetId)
    if (presetId === 'custom') return
    const preset = SOURCE_PRESETS.find(({ id }) => id === presetId)
    if (!preset) return
    setSources({ ...preset.values })
    setSourceSaveStatus('idle')
    setSourceTestStatus('idle')
    setSourceTestError(null)
  }

  async function handleSave(): Promise<void> {
    if (form.enabled && !isValidProxyUrl(form.url)) {
      setSaveStatus('error')
      return
    }
    setSaveStatus('saving')
    try {
      await window.polykit.settings.set({
        proxy: proxyPayload(),
      })
      setSaveStatus('saved')
      setTimeout(() => setSaveStatus('idle'), 2500)
    } catch {
      setSaveStatus('error')
      setTimeout(() => setSaveStatus('idle'), 3000)
    }
  }

  async function handleTest(): Promise<void> {
    if (form.enabled && !isValidProxyUrl(form.url)) {
      setTestStatus('invalid')
      return
    }
    setTestStatus('testing')
    setTestError(null)
    try {
      // Test exactly what is currently in the form, even when Save has not
      // been pressed yet. The setting is server-owned and applies immediately.
      await window.polykit.settings.set({ proxy: proxyPayload() })
      const result = await window.polykit.settings.testProxy()
      setTestStatus(result.ok ? 'ok' : 'error')
      if (!result.ok) setTestError(result.error ?? t('settings.testError'))
    } catch (error) {
      setTestStatus('error')
      setTestError(error instanceof Error ? error.message : String(error))
    }
  }

  async function handleSaveSources(): Promise<void> {
    const payload = sourcePayload()
    if (Object.values(payload).some((value) => !isValidSourceUrl(value))) {
      setSourceSaveStatus('error')
      return
    }
    setSourceSaveStatus('saving')
    try {
      await window.polykit.settings.set({ sources: payload })
      setSourceSaveStatus('saved')
      setTimeout(() => setSourceSaveStatus('idle'), 2500)
    } catch {
      setSourceSaveStatus('error')
      setTimeout(() => setSourceSaveStatus('idle'), 3000)
    }
  }

  async function handleTestSource(kind: SourceKind): Promise<void> {
    const payload = sourcePayload()
    if (!isValidSourceUrl(payload[kind])) {
      setSourceTestStatus('error')
      setSourceTestError(t('settings.sourceUrlInvalid'))
      return
    }
    setSourceTestKind(kind)
    setSourceTestStatus('idle')
    setSourceTestError(null)
    try {
      await window.polykit.settings.set({ sources: payload })
      const testSources = window.polykit.settings.testSources
      if (typeof testSources !== 'function') {
        throw new Error(t('settings.sourceTestUnavailable'))
      }
      const result = await testSources(kind === 'huggingfaceEndpoint' ? 'huggingface' : kind === 'pypiIndexUrl' ? 'pypi' : 'pytorch')
      setSourceTestStatus(result.ok ? 'ok' : 'error')
      if (!result.ok) setSourceTestError(result.error ?? t('settings.sourceError'))
    } catch (error) {
      setSourceTestStatus('error')
      setSourceTestError(error instanceof Error ? error.message : String(error))
    } finally {
      setSourceTestKind(null)
    }
  }

  const statusButtonClass = (status: 'idle' | 'saving' | 'saved' | 'error'): string | undefined => {
    if (status === 'saved') return 'border border-emerald-500/30 bg-emerald-500/10 text-emerald-300 hover:bg-emerald-500/15'
    if (status === 'error') return 'border border-destructive/30 bg-destructive/10 text-destructive hover:bg-destructive/15'
    return undefined
  }

  return (
    <SettingsSection title={t('settings.network')} subtitle={t('settings.networkSubtitle')}>
      <div className="grid gap-4">
        <SettingsCard title={t('settings.proxy')} description={t('settings.proxyDescription')}>
          <p className="px-5 pt-4 text-xs leading-5 text-muted-foreground">
            {t('settings.proxyServerHint')}
          </p>
          <SettingsRow label={t('settings.enableProxy')}>
            <Switch
              checked={form.enabled}
              onCheckedChange={(value) => update({ enabled: value })}
              aria-label={t('settings.enableProxy')}
            />
          </SettingsRow>
          <SettingsRow label={t('settings.proxyUrl')} description={t('settings.proxyUrlHint')}>
            <Input
              type="text"
              value={form.url}
              onChange={(event) => update({ url: event.target.value })}
              placeholder="http://127.0.0.1:7890"
              spellCheck={false}
              aria-label={t('settings.proxyUrl')}
              className="h-8 w-full font-mono text-xs"
            />
          </SettingsRow>
          <SettingsRow label={t('settings.proxyUsername')}>
            <Input
              type="text"
              value={form.username}
              onChange={(event) => update({ username: event.target.value })}
              spellCheck={false}
              autoComplete="off"
              aria-label={t('settings.proxyUsername')}
              className="h-8 w-full text-xs"
            />
          </SettingsRow>
          <SettingsRow label={t('settings.proxyPassword')}>
            <div className="relative min-w-0">
              <Input
                type={passwordVisible ? 'text' : 'password'}
                value={form.password}
                onChange={(event) => update({ password: event.target.value })}
                autoComplete="new-password"
                spellCheck={false}
                aria-label={t('settings.proxyPassword')}
                className="h-8 w-full pr-9 text-xs"
              />
              <Button
                type="button"
                variant="ghost"
                size="icon"
                className="absolute right-0.5 top-0.5 size-7 text-muted-foreground"
                onClick={() => setPasswordVisible((value) => !value)}
                title={passwordVisible ? t('settings.hideToken') : t('settings.showToken')}
                aria-label={passwordVisible ? t('settings.hideToken') : t('settings.showToken')}
              >
                {passwordVisible ? <EyeOff className="size-3.5" /> : <Eye className="size-3.5" />}
              </Button>
            </div>
          </SettingsRow>
          <SettingsRow label={t('settings.proxyBypass')} description={t('settings.proxyBypassHint')}>
            <Input
              type="text"
              value={form.bypass}
              onChange={(event) => update({ bypass: event.target.value })}
              placeholder="internal.example.com"
              spellCheck={false}
              aria-label={t('settings.proxyBypass')}
              className="h-8 w-full font-mono text-xs"
            />
          </SettingsRow>
          <div className="flex items-center justify-end gap-2 px-5 py-4">
            <Button
              type="button"
              size="sm"
              variant="secondary"
              onClick={() => { void handleTest() }}
              disabled={saveStatus === 'saving' || testStatus === 'testing'}
              className={
                testStatus === 'ok'
                  ? 'border border-emerald-500/30 bg-emerald-500/10 text-emerald-300 hover:bg-emerald-500/15'
                  : (testStatus === 'error' || testStatus === 'invalid')
                    ? 'border border-destructive/30 bg-destructive/10 text-destructive hover:bg-destructive/15'
                    : undefined
              }
            >
              {testStatus === 'testing' ? (
                <>
                  <Loader2 className="mr-1.5 size-3.5 animate-spin" />
                  {t('settings.testing')}
                </>
              ) : testStatus === 'ok'
                ? t('settings.testOk')
                : (testStatus === 'error' || testStatus === 'invalid')
                  ? t('settings.testError')
                  : t('settings.testConnection')}
            </Button>
            <Button
              type="button"
              size="sm"
              onClick={() => { void handleSave() }}
              disabled={saveStatus === 'saving' || testStatus === 'testing'}
              className={statusButtonClass(saveStatus)}
            >
              {saveStatus === 'saving' ? t('settings.saving') : saveStatus === 'saved' ? t('settings.saved') : saveStatus === 'error' ? t('settings.failed') : t('common.save')}
            </Button>
          </div>
          {testError && (
            <p className="px-5 pb-4 text-xs text-destructive" role="status">
              {testError}
            </p>
          )}
        </SettingsCard>

        <SettingsCard title={t('settings.downloadSources')} description={t('settings.downloadSourcesDescription')}>
          <p className="px-5 pt-4 text-xs leading-5 text-muted-foreground">
            {t('settings.sourceServerHint')}
          </p>
          <SettingsRow label={t('settings.sourcePreset')} description={t('settings.sourcePresetHint')}>
            <Select value={selectedPreset} onValueChange={(value) => applySourcePreset(value as SourcePresetId)}>
              <SelectTrigger className="w-full text-xs" aria-label={t('settings.sourcePreset')}>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {SOURCE_PRESETS.map(({ id, labelKey }) => (
                  <SelectItem key={id} value={id}>{t(labelKey)}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </SettingsRow>
          <SettingsRow label={t('settings.huggingfaceEndpoint')} description={t('settings.huggingfaceEndpointHint')}>
            <div className="flex items-center gap-2">
              <Input
                type="text"
                value={sources.huggingfaceEndpoint}
                onChange={(event) => updateSource('huggingfaceEndpoint', event.target.value)}
                placeholder="https://huggingface.co"
                spellCheck={false}
                aria-label={t('settings.huggingfaceEndpoint')}
                className="h-8 min-w-0 flex-1 font-mono text-xs"
              />
              <Button type="button" size="sm" variant="secondary" onClick={() => { void handleTestSource('huggingfaceEndpoint') }} disabled={sourceTestKind !== null}>
                {sourceTestKind === 'huggingfaceEndpoint' ? <Loader2 className="size-3.5 animate-spin" /> : t('settings.testSource')}
              </Button>
            </div>
          </SettingsRow>
          <SettingsRow label={t('settings.pypiIndexUrl')} description={t('settings.pypiIndexUrlHint')}>
            <div className="flex items-center gap-2">
              <Input
                type="text"
                value={sources.pypiIndexUrl}
                onChange={(event) => updateSource('pypiIndexUrl', event.target.value)}
                placeholder="https://pypi.org/simple"
                spellCheck={false}
                aria-label={t('settings.pypiIndexUrl')}
                className="h-8 min-w-0 flex-1 font-mono text-xs"
              />
              <Button type="button" size="sm" variant="secondary" onClick={() => { void handleTestSource('pypiIndexUrl') }} disabled={sourceTestKind !== null}>
                {sourceTestKind === 'pypiIndexUrl' ? <Loader2 className="size-3.5 animate-spin" /> : t('settings.testSource')}
              </Button>
            </div>
          </SettingsRow>
          <SettingsRow label={t('settings.pytorchIndexUrl')} description={t('settings.pytorchIndexUrlHint')}>
            <div className="flex items-center gap-2">
              <Input
                type="text"
                value={sources.pytorchIndexUrl}
                onChange={(event) => updateSource('pytorchIndexUrl', event.target.value)}
                placeholder="https://download.pytorch.org/whl/{tag}"
                spellCheck={false}
                aria-label={t('settings.pytorchIndexUrl')}
                className="h-8 min-w-0 flex-1 font-mono text-xs"
              />
              <Button type="button" size="sm" variant="secondary" onClick={() => { void handleTestSource('pytorchIndexUrl') }} disabled={sourceTestKind !== null}>
                {sourceTestKind === 'pytorchIndexUrl' ? <Loader2 className="size-3.5 animate-spin" /> : t('settings.testSource')}
              </Button>
            </div>
          </SettingsRow>
          <div className="flex items-center justify-end gap-2 px-5 py-4">
            <Button type="button" size="sm" onClick={() => { void handleSaveSources() }} disabled={sourceSaveStatus === 'saving' || sourceTestKind !== null}>
              {sourceSaveStatus === 'saving' ? t('settings.saving') : sourceSaveStatus === 'saved' ? t('settings.saved') : sourceSaveStatus === 'error' ? t('settings.failed') : t('common.save')}
            </Button>
          </div>
          {sourceTestStatus === 'ok' && <p className="px-5 pb-4 text-xs text-emerald-300" role="status">{t('settings.sourceOk')}</p>}
          {sourceTestError && <p className="px-5 pb-4 text-xs text-destructive" role="status">{sourceTestError}</p>}
        </SettingsCard>
      </div>
    </SettingsSection>
  )
}

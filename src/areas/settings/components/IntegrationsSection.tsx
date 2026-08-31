import { useEffect, useState } from 'react'
import { Eye, EyeOff, X } from 'lucide-react'

import { Button, Input } from '@shared/components/ui'
import { useI18n } from '@shared/i18n'
import { SettingsCard, SettingsRow, SettingsSection } from './SettingsLayout'

export function IntegrationsSection(): JSX.Element {
  const { t } = useI18n()
  const [token, setToken] = useState('')
  const [visible, setVisible] = useState(false)
  const [status, setStatus] = useState<'idle' | 'saving' | 'saved' | 'error'>('idle')

  useEffect(() => {
    window.polykit.settings.get().then((settings) => {
      setToken(settings.hfToken ?? '')
    })
  }, [])

  async function handleSave() {
    setStatus('saving')
    try {
      await window.polykit.settings.set({ hfToken: token.trim() })
      setStatus('saved')
      setTimeout(() => setStatus('idle'), 2500)
    } catch {
      setStatus('error')
      setTimeout(() => setStatus('idle'), 3000)
    }
  }

  async function handleClear() {
    setToken('')
    setStatus('saving')
    try {
      await window.polykit.settings.set({ hfToken: '' })
      setStatus('saved')
      setTimeout(() => setStatus('idle'), 2500)
    } catch {
      setStatus('error')
      setTimeout(() => setStatus('idle'), 3000)
    }
  }

  return (
    <div className="flex flex-col gap-10">
      <SettingsSection title={t('settings.integrations')} subtitle={t('settings.integrationsSubtitle')}>
        <div className="grid gap-4">
          <SettingsCard
            title="Hugging Face Hub"
            description={t('settings.hfDownloadDescription')}
          >
            <SettingsRow label={t('settings.accessToken')} description={t('settings.readPermission')}>
              <div className="flex w-full items-center gap-2">
                <div className="relative min-w-0 flex-1">
                  <Input
                    type={visible ? 'text' : 'password'}
                    value={token}
                    onChange={(event) => { setToken(event.target.value); setStatus('idle') }}
                    onKeyDown={(event) => { if (event.key === 'Enter') void handleSave() }}
                    placeholder="hf_…"
                    spellCheck={false}
                    aria-label={t('settings.hfAccessToken')}
                    className="h-8 pr-9 font-mono text-xs"
                  />
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon"
                    className="absolute right-0.5 top-0.5 size-7 text-muted-foreground"
                    onClick={() => setVisible((value) => !value)}
                    title={visible ? t('settings.hideToken') : t('settings.showToken')}
                    aria-label={visible ? t('settings.hideToken') : t('settings.showToken')}
                  >
                    {visible ? <EyeOff className="size-3.5" /> : <Eye className="size-3.5" />}
                  </Button>
                </div>

                {token && (
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon"
                    className="size-8 text-muted-foreground hover:text-destructive"
                    onClick={() => { void handleClear() }}
                    title={t('settings.removeToken')}
                    aria-label={t('settings.removeToken')}
                  >
                    <X className="size-4" />
                  </Button>
                )}

                <Button
                  type="button"
                  size="sm"
                  onClick={() => { void handleSave() }}
                  disabled={status === 'saving'}
                  className={
                    status === 'saved'
                      ? 'border border-emerald-500/30 bg-emerald-500/10 text-emerald-300 hover:bg-emerald-500/15 hover:text-emerald-300'
                      : status === 'error'
                        ? 'border border-destructive/30 bg-destructive/10 text-destructive hover:bg-destructive/15'
                        : undefined
                  }
                >
                  {status === 'saving' ? t('settings.saving') : status === 'saved' ? t('settings.saved') : status === 'error' ? t('settings.failed') : t('common.save')}
                </Button>
              </div>
            </SettingsRow>
          </SettingsCard>
        </div>
      </SettingsSection>
    </div>
  )
}

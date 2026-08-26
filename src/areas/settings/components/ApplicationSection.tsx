import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue, Switch } from '@shared/components/ui'
import { useI18n } from '@shared/i18n'
import { useAppStore, type Language } from '@shared/stores/appStore'
import { SettingsCard, SettingsRow, SettingsSection } from './SettingsLayout'

export function ApplicationSection(): JSX.Element {
  const {
    showRamIndicator: showResourceMonitor,
    setShowRamIndicator: setShowResourceMonitor,
    language,
    setLanguage,
  } = useAppStore()
  const { t } = useI18n()

  return (
    <SettingsSection title={t('settings.application')} subtitle={t('settings.applicationSubtitle')}>
      <SettingsCard title={t('settings.interface')}>
        <SettingsRow label={t('settings.language')} description={t('settings.languageHint')}>
          <Select value={language} onValueChange={(value) => setLanguage(value as Language)}>
            <SelectTrigger className="w-40" aria-label={t('settings.language')}>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="zh-CN">{t('settings.chinese')}</SelectItem>
              <SelectItem value="en-US">{t('settings.english')}</SelectItem>
            </SelectContent>
          </Select>
        </SettingsRow>
        <SettingsRow label={t('settings.resourceMonitor')} description={t('settings.resourceMonitorDescription')}>
          <Switch
            checked={showResourceMonitor}
            onCheckedChange={setShowResourceMonitor}
            aria-label={t('settings.resourceMonitor')}
          />
        </SettingsRow>
      </SettingsCard>
    </SettingsSection>
  )
}

import { useI18n } from '@shared/i18n'
import { SettingsCard, SettingsLinkButton, SettingsRow, SettingsSection } from './SettingsLayout'

export function AboutSection(): JSX.Element {
  const { t } = useI18n()

  return (
    <SettingsSection title={t('settings.about')} subtitle={t('settings.aboutSubtitle')}>
      <SettingsCard>
        <SettingsRow label={t('settings.sourceCode')} description={t('settings.sourceCodeDescription')}>
          <SettingsLinkButton label={t('settings.open')} href="https://github.com/mousebar01/PolyKit" />
        </SettingsRow>
        <SettingsRow label={t('settings.license')} description={t('settings.licenseDescription')}>
          <SettingsLinkButton label={t('settings.view')} href="https://github.com/mousebar01/PolyKit/blob/main/LICENSE" />
        </SettingsRow>
      </SettingsCard>
    </SettingsSection>
  )
}

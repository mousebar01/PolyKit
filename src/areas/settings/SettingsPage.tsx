import { useEffect, useState } from 'react'
import { Bot, Globe, HardDrive, Info, Plug, SlidersHorizontal, type LucideIcon } from 'lucide-react'

import { Button } from '@shared/components/ui/button'
import { useI18n, type TranslationKey } from '@shared/i18n'
import { useNavStore, type SettingsSection } from '@shared/stores/navStore'
import { StorageSection } from './components/StorageSection'
import { AboutSection } from './components/AboutSection'
import { IntegrationsSection } from './components/IntegrationsSection'
import { ApplicationSection } from './components/ApplicationSection'
import { NetworkSection } from './components/NetworkSection'
import { AgentSection } from './components/AgentSection'

type Section = SettingsSection

const SECTIONS: { id: Section; label: TranslationKey; icon: LucideIcon }[] = [
  { id: 'application', label: 'settings.application', icon: SlidersHorizontal },
  { id: 'agent', label: 'settings.agent', icon: Bot },
  { id: 'storage', label: 'settings.storage', icon: HardDrive },
  { id: 'integrations', label: 'settings.integrations', icon: Plug },
  { id: 'network', label: 'settings.network', icon: Globe },
  { id: 'about', label: 'settings.about', icon: Info },
]

export default function SettingsPage(): JSX.Element {
  const [section, setSection] = useState<Section>('application')
  const { t } = useI18n()
  const pendingSection = useNavStore((state) => state.pendingSettingsSection)
  const consumeSettingsSection = useNavStore((state) => state.consumeSettingsSection)

  useEffect(() => {
    if (pendingSection) {
      setSection(pendingSection)
      consumeSettingsSection()
    }
  }, [consumeSettingsSection, pendingSection])

  return (
    <div className="flex h-full bg-background">
      <nav className="flex w-[188px] shrink-0 flex-col gap-1 border-r border-border/45 bg-card/65 px-2 py-3" aria-label={t('settings.title')}>
        <p className="mb-2 px-2 text-[10px] font-semibold uppercase tracking-[0.08em] text-muted-foreground">{t('settings.title')}</p>
        {SECTIONS.map((item) => {
          const Icon = item.icon
          const active = section === item.id
          return (
            <Button
              key={item.id}
              type="button"
              variant="ghost"
              aria-current={active ? 'page' : undefined}
              onClick={() => setSection(item.id)}
              className={active
                ? 'relative h-8 justify-start gap-2.5 rounded-md bg-primary/10 px-2.5 text-primary hover:bg-primary/15 hover:text-primary'
                : 'relative h-8 justify-start gap-2.5 rounded-md px-2.5 text-muted-foreground hover:bg-muted hover:text-foreground'}
            >
              <Icon className="size-4" />
              {t(item.label)}
            </Button>
          )
        })}
      </nav>

      <main className="flex-1 overflow-y-auto bg-background">
        <div className="mx-auto max-w-6xl p-5 lg:p-6">
          {section === 'application' && <ApplicationSection />}
          {section === 'agent' && <AgentSection />}
          {section === 'storage' && <StorageSection />}
          {section === 'integrations' && <IntegrationsSection />}
          {section === 'network' && <NetworkSection />}
          {section === 'about' && <AboutSection />}
        </div>
      </main>
    </div>
  )
}

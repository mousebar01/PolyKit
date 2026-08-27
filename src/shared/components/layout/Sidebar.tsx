import { Bot, Box, Package, Settings, Workflow, type LucideIcon } from 'lucide-react'

import { Button } from '@shared/components/ui/button'
import { useNavStore, type Page } from '@shared/stores/navStore'
import { useI18n, type TranslationKey } from '@shared/i18n'
import { cn } from '@shared/lib/utils'

const NAV_ITEMS: { id: Page; label: TranslationKey; icon: LucideIcon }[] = [
  { id: 'assets', label: 'nav.assets', icon: Box },
  { id: 'workflows', label: 'nav.workflows', icon: Workflow },
  { id: 'nodePacks', label: 'nav.nodePacks', icon: Package },
  { id: 'agent', label: 'nav.agent', icon: Bot },
  { id: 'settings', label: 'nav.settings', icon: Settings },
]

export default function Sidebar(): JSX.Element {
  const { currentPage, navigate } = useNavStore()
  const { t } = useI18n()

  return (
    <aside className="flex w-[72px] shrink-0 flex-col gap-1 border-r border-border bg-card/70 px-2 py-4" aria-label="Primary navigation">
      {NAV_ITEMS.map((item) => {
        const active = currentPage === item.id
        const Icon = item.icon
        return (
          <Button
            key={item.id}
            type="button"
            variant="ghost"
            title={t(item.label)}
            aria-current={active ? 'page' : undefined}
            onClick={() => navigate(item.id)}
            className={cn(
              'relative h-14 flex-col gap-1 rounded-lg px-1 text-[10px] leading-none',
              active
                ? 'bg-primary/10 text-primary hover:bg-primary/15 hover:text-primary'
                : 'text-muted-foreground hover:text-foreground',
            )}
          >
            <Icon className="size-5" strokeWidth={1.7} />
            <span>{t(item.label)}</span>
          </Button>
        )
      })}
    </aside>
  )
}

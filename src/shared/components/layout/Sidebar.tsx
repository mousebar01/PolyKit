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
    <aside className="flex w-[72px] shrink-0 flex-col bg-background px-1.5 py-2" aria-label="Primary navigation">
      <nav className="flex flex-col gap-1" aria-label="Workspace navigation">
        {NAV_ITEMS.map((item) => {
          const active = currentPage === item.id
          const Icon = item.icon
          return (
            <Button
              key={item.id}
              type="button"
              variant="ghost"
              title={t(item.label)}
              aria-label={t(item.label)}
              aria-current={active ? 'page' : undefined}
              onClick={() => navigate(item.id)}
              className={cn(
                'h-12 w-full flex-col gap-1 rounded-md px-1 text-[10px] leading-none text-muted-foreground transition-colors hover:bg-muted hover:text-foreground',
                active && 'bg-primary/10 text-primary hover:bg-primary/15 hover:text-primary shadow-sm shadow-primary/10',
              )}
            >
              <Icon className="size-[18px]" strokeWidth={1.8} />
              <span className="max-w-full truncate">{t(item.label)}</span>
            </Button>
          )
        })}
      </nav>
    </aside>
  )
}

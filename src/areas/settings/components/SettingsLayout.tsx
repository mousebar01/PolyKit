import type { ReactNode } from 'react'
import { ExternalLink, FolderOpen } from 'lucide-react'

import {
  Button,
  Card,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@shared/components/ui'
import { useI18n } from '@shared/i18n'

export function SettingsSection({
  title,
  subtitle,
  children,
}: {
  title: string
  subtitle: string
  children: ReactNode
}): JSX.Element {
  return (
    <section className="space-y-5">
      <header className="space-y-1.5">
        <h2 className="text-2xl font-semibold tracking-tight text-foreground">{title}</h2>
        <p className="max-w-2xl text-sm text-muted-foreground">{subtitle}</p>
      </header>
      {children}
    </section>
  )
}

export function SettingsCard({
  title,
  description,
  children,
}: {
  title?: string
  description?: string
  children: ReactNode
}): JSX.Element {
  return (
    <Card className="overflow-hidden border-divider bg-card/75">
      {(title || description) && (
        <CardHeader className="bg-card/35 px-5 py-4">
          {title && <CardTitle>{title}</CardTitle>}
          {description && <CardDescription>{description}</CardDescription>}
        </CardHeader>
      )}
      <div className="divide-y divide-divider">{children}</div>
    </Card>
  )
}

export function SettingsRow({
  label,
  description,
  children,
}: {
  label: string
  description?: string
  children: ReactNode
}): JSX.Element {
  return (
    <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-3 px-5 py-4">
      <div className="min-w-0 flex-1 space-y-0.5">
        <p className="text-sm font-medium text-foreground">{label}</p>
        {description && <p className="text-xs text-muted-foreground">{description}</p>}
      </div>
      <div className="w-full max-w-[360px] shrink-0 sm:ml-auto">{children}</div>
    </div>
  )
}

export function SettingsPathRow({
  label,
  description,
  value,
  onBrowse,
}: {
  label: string
  description?: string
  value: string
  onBrowse?: () => void
}): JSX.Element {
  const { t } = useI18n()
  return (
    <div className="space-y-2 px-5 py-4">
      <div className="flex items-start justify-between gap-4">
        <div className="space-y-0.5">
          <p className="text-sm font-medium text-foreground">{label}</p>
          {description && <p className="text-xs text-muted-foreground">{description}</p>}
        </div>
        {onBrowse && (
          <Button type="button" variant="secondary" size="sm" onClick={onBrowse}>
            <FolderOpen className="mr-1.5 size-3.5" />
            {t('settings.browse')}
          </Button>
        )}
      </div>
      <div className="flex items-center gap-2.5 rounded-md border border-divider bg-muted/40 px-3 py-2.5">
        <FolderOpen className="size-3.5 shrink-0 text-muted-foreground" />
        <span className="truncate font-mono text-xs text-muted-foreground">{value}</span>
      </div>
    </div>
  )
}

export function SettingsLinkButton({ label, href }: { label: string; href: string }): JSX.Element {
  return (
    <Button
      type="button"
      variant="secondary"
      size="sm"
      onClick={() => window.open(href, '_blank', 'noopener,noreferrer')}
    >
      {label}
      <ExternalLink className="ml-1.5 size-3.5" />
    </Button>
  )
}

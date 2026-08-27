import { useAppStore } from '@shared/stores/appStore'
import { useI18n } from '@shared/i18n'
import { PolyKitWordmark } from '@shared/components/brand/PolyKitWordmark'
import ResourceIndicator from './ResourceIndicator'

export default function TopBar(): JSX.Element {
  const { showRamIndicator, backendStatus, apiUrl } = useAppStore()
  const { t } = useI18n()

  return (
    <header className="drag-region flex h-11 shrink-0 items-center rounded-md bg-card px-3">
      <div className="no-drag flex min-w-0 items-center gap-3">
        <PolyKitWordmark className="text-[15px]" />
        <span className="hidden text-[11px] text-muted-foreground sm:inline">3D workspace</span>
      </div>

      <div className="flex-1" />

      <div
        className="no-drag mr-2 flex items-center gap-2 rounded-md border border-border bg-muted/60 px-2.5 py-1 text-[10px] text-muted-foreground"
        title={apiUrl || t('top.serverOffline')}
      >
        <span className={`size-1.5 rounded-full ${backendStatus === 'ready' ? 'bg-sky-400' : 'animate-pulse bg-amber-400'}`} />
        <span>{t('top.server')}</span>
        <span className={backendStatus === 'ready' ? 'text-sky-300' : 'text-amber-300'}>
          {backendStatus === 'ready' ? t('top.serverConnected') : t('top.serverOffline')}
        </span>
      </div>

      {showRamIndicator && backendStatus === 'ready' && <ResourceIndicator apiUrl={apiUrl} />}
    </header>
  )
}

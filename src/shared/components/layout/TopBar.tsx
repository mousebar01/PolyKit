import { useAppStore } from '@shared/stores/appStore'
import { useI18n } from '@shared/i18n'
import { PolyKitWordmark } from '@shared/components/brand/PolyKitWordmark'
import ResourceIndicator from './ResourceIndicator'

export default function TopBar(): JSX.Element {
  const { showRamIndicator, backendStatus, apiUrl } = useAppStore()
  const { t } = useI18n()

  return (
    <header className="drag-region flex h-12 shrink-0 items-center border-b border-border bg-card/80 px-5 backdrop-blur-sm">
      <div className="no-drag flex items-center">
        <PolyKitWordmark className="text-sm" />
      </div>

      <div className="flex-1" />

      <div
        className="no-drag mr-4 flex items-center gap-2 rounded-full border border-border/80 bg-muted/50 px-3 py-1.5 text-[11px] text-muted-foreground"
        title={apiUrl || t('top.serverOffline')}
      >
        <span className={`size-1.5 rounded-full ${backendStatus === 'ready' ? 'bg-emerald-400' : 'animate-pulse bg-amber-400'}`} />
        <span>{t('top.server')}</span>
        <span className={backendStatus === 'ready' ? 'text-emerald-300' : 'text-amber-300'}>
          {backendStatus === 'ready' ? t('top.serverConnected') : t('top.serverOffline')}
        </span>
      </div>

      {showRamIndicator && backendStatus === 'ready' && <ResourceIndicator apiUrl={apiUrl} />}
    </header>
  )
}

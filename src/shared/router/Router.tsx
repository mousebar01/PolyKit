import { Suspense } from 'react'
import { useNavStore } from '@shared/stores/navStore'
import { ROUTES } from './routes'

function RouteLoading({ frame }: { frame: boolean }): JSX.Element {
  return (
    <div className={`flex flex-1 items-center justify-center rounded-xl ${frame ? 'border border-border/35 bg-card/20' : 'bg-background'}`} role="status" aria-live="polite">
      <div className="flex items-center gap-2.5 rounded-lg border border-border bg-card px-3 py-2 text-xs text-muted-foreground shadow-sm">
        <span className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-muted border-t-primary" />
        Loading…
      </div>
    </div>
  )
}

export default function Router(): JSX.Element {
  const currentPage = useNavStore((s) => s.currentPage)
  const { component: Page, wrapperClass, frame = false } = ROUTES[currentPage]
  const frameClass = frame ? 'rounded-xl border border-border/35 bg-card/20' : ''

  return (
    <Suspense fallback={<RouteLoading frame={frame} />}>
      <div className={`${wrapperClass} ${frameClass}`}>
        <Page />
      </div>
    </Suspense>
  )
}

import { Suspense } from 'react'
import { useNavStore } from '@shared/stores/navStore'
import { ROUTES } from './routes'

function RouteLoading(): JSX.Element {
  return (
    <div className="flex flex-1 items-center justify-center overflow-hidden rounded-lg bg-background" role="status" aria-live="polite">
      <div className="flex items-center gap-2.5 rounded-lg border border-divider bg-card px-3 py-2 text-xs text-muted-foreground">
        <span className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-muted border-t-primary" />
        Loading…
      </div>
    </div>
  )
}

export default function Router(): JSX.Element {
  const currentPage = useNavStore((s) => s.currentPage)
  const { component: Page, wrapperClass } = ROUTES[currentPage]

  return (
    <Suspense fallback={<RouteLoading />}>
      <div className={wrapperClass}>
        <Page />
      </div>
    </Suspense>
  )
}

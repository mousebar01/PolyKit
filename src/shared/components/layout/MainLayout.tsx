import TopBar from './TopBar'
import Sidebar from './Sidebar'
import Router from '@shared/router/Router'

export default function MainLayout(): JSX.Element {

  return (
    <div className="app-bg h-full p-1.5">
      <div className="flex h-full flex-col overflow-hidden rounded-xl bg-background">
        <TopBar />

        <div className="flex min-h-0 flex-1 overflow-hidden">
          <Sidebar />

          <main className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden rounded-lg bg-background p-1.5">
            <Router />
          </main>
        </div>
      </div>
    </div>
  )
}

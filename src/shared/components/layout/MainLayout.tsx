import TopBar from './TopBar'
import Sidebar from './Sidebar'
import Router from '@shared/router/Router'

export default function MainLayout(): JSX.Element {

  return (
    <div className="app-bg flex h-full flex-col gap-1 overflow-hidden p-1">
      <TopBar />

      <div className="flex min-h-0 flex-1 gap-1 overflow-hidden">
        <Sidebar />

        <main className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden">
          <Router />
        </main>
      </div>
    </div>
  )
}

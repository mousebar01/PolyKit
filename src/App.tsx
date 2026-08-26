import { useEffect } from 'react'
import { useAppStore } from '@shared/stores/appStore'
import FirstRunSetup from '@areas/setup/FirstRunSetup'
import MainLayout from '@shared/components/layout/MainLayout'
import { ErrorModal } from '@shared/components/ui/ErrorModal'
import { Toast } from '@shared/components/ui/Toast'

export default function App(): JSX.Element {
  const { checkSetup, setupStatus, initApp, backendStatus, showError } = useAppStore()

  useEffect(() => {
    checkSetup()
    window.polykit.app.onError((message) => showError(message))
    return () => {
      window.polykit.app.offError()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- run once on mount; store actions are stable
  }, [])

  useEffect(() => {
    if (setupStatus === 'done') initApp()
    // eslint-disable-next-line react-hooks/exhaustive-deps -- react to setup transition only; initApp is stable
  }, [setupStatus])

  if (backendStatus === 'ready') return (
    <>
      <MainLayout />
      <Toast />
      <ErrorModal />
    </>
  )
  return (
    <>
      <FirstRunSetup />
      <Toast />
      <ErrorModal />
    </>
  )
}

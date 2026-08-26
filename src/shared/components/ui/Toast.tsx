import { useEffect } from 'react'
import { Toaster, toast as sonnerToast } from 'sonner'

import { useAppStore } from '@shared/stores/appStore'

export function Toast(): JSX.Element {
  const { toast, hideToast } = useAppStore()

  useEffect(() => {
    if (!toast) return
    sonnerToast.warning(toast.message, {
      id: toast.id,
      duration: toast.durationMs ?? 2800,
    })
    hideToast()
  }, [toast, hideToast])

  return (
    <Toaster
      position="bottom-right"
      closeButton
      theme="dark"
      toastOptions={{
        classNames: {
          toast: 'border-border bg-popover text-popover-foreground',
          description: 'text-muted-foreground',
          closeButton: 'border-border bg-background text-muted-foreground',
        },
      }}
    />
  )
}

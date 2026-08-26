import { useState } from 'react'
import { AlertTriangle, Check, Copy } from 'lucide-react'

import { useAppStore } from '@shared/stores/appStore'
import { Button } from './button'
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from './dialog'

export function ErrorModal(): JSX.Element {
  const { errorModal, hideError } = useAppStore()
  const [copied, setCopied] = useState(false)

  const handleCopy = (): void => {
    if (!errorModal) return
    navigator.clipboard.writeText(errorModal).then(() => {
      setCopied(true)
      window.setTimeout(() => setCopied(false), 2000)
    })
  }

  return (
    <Dialog open={Boolean(errorModal)} onOpenChange={(open) => { if (!open) hideError() }}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <div className="flex items-center gap-2.5">
            <span className="flex size-8 items-center justify-center rounded-full bg-destructive/10 text-destructive">
              <AlertTriangle className="size-4" />
            </span>
            <DialogTitle>Error</DialogTitle>
          </div>
        </DialogHeader>

        <pre className="max-h-60 overflow-y-auto whitespace-pre-wrap break-words rounded-md border border-destructive/20 bg-destructive/5 p-3 font-mono text-xs leading-relaxed text-destructive select-text">
          {errorModal}
        </pre>

        <DialogFooter>
          <Button type="button" variant="secondary" onClick={handleCopy} disabled={!errorModal}>
            {copied ? <Check className="mr-1.5 size-4" /> : <Copy className="mr-1.5 size-4" />}
            {copied ? 'Copied' : 'Copy'}
          </Button>
          <Button type="button" onClick={hideError}>Close</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

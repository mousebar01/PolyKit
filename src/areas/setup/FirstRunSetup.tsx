import { useEffect, useState } from 'react'
import { AlertTriangle, FolderOpen, RotateCcw } from 'lucide-react'

import { Button } from '@shared/components/ui/button'
import { Card } from '@shared/components/ui/card'
import { useAppStore, type SetupProgress } from '@shared/stores/appStore'
import { PolyKitWordmark } from '@shared/components/brand/PolyKitWordmark'

function AppHeader(): JSX.Element {
  return (
    <div className="mb-8 text-center">
      <PolyKitWordmark className="mb-1.5 text-3xl" />
      <p className="text-sm text-muted-foreground">AI-powered 3D mesh generation</p>
    </div>
  )
}

function ProgressTrack({ value, pulse = false }: { value: number; pulse?: boolean }): JSX.Element {
  return (
    <div className="h-1.5 overflow-hidden rounded-full bg-muted" aria-hidden="true">
      <div
        className={`h-full rounded-full bg-primary transition-all duration-300 ${pulse ? 'animate-pulse' : ''}`}
        style={{ width: `${Math.max(0, Math.min(100, value))}%` }}
      />
    </div>
  )
}

function CheckingPanel(): JSX.Element {
  return (
    <Card className="w-80 space-y-4 p-6 shadow-none" role="status" aria-live="polite">
      <p className="text-sm font-medium text-foreground">Checking environment…</p>
      <ProgressTrack value={30} pulse />
    </Card>
  )
}

function ChoosePathPanel({
  defaultPath,
  platform,
  arch,
  onConfirm,
}: {
  defaultPath: string
  platform: string
  arch: string
  onConfirm: (path: string) => void
}): JSX.Element {
  const [selectedPath, setSelectedPath] = useState(defaultPath || '')

  useEffect(() => {
    if (defaultPath && !selectedPath) setSelectedPath(defaultPath)
    // eslint-disable-next-line react-hooks/exhaustive-deps -- only sync when defaultPath arrives, not on user edits
  }, [defaultPath])

  async function handleBrowse(): Promise<void> {
    const picked = await window.polykit.fs.selectDirectory(selectedPath || undefined)
    if (picked) setSelectedPath(picked)
  }

  return (
    <Card className="w-80 space-y-4 p-6 shadow-none">
      <div className="space-y-1">
        <p className="text-sm font-medium text-foreground">Choose a data folder</p>
        <p className="text-xs leading-relaxed text-muted-foreground">
          Models can be several GB each. Choose a folder with plenty of free space.
          {platform === 'darwin' && arch === 'arm64'
            ? ' Apple Silicon is supported on this build.'
            : ' A fast local SSD is recommended.'}
        </p>
      </div>

      <div className="flex items-center gap-2">
        <div className="flex min-w-0 flex-1 items-center gap-2 rounded-md border border-border bg-muted/40 px-3 py-2.5">
          <FolderOpen className="size-3.5 shrink-0 text-muted-foreground" />
          <p className="truncate font-mono text-xs text-muted-foreground" title={selectedPath}>
            {selectedPath || 'No folder selected'}
          </p>
        </div>
        <Button type="button" variant="secondary" size="sm" onClick={handleBrowse}>Browse…</Button>
      </div>

      <Button type="button" className="w-full" onClick={() => onConfirm(selectedPath)} disabled={!selectedPath}>
        Continue
      </Button>
    </Card>
  )
}

const STEPS = [
  { key: 'enabling-site', label: 'Preparing Python' },
  { key: 'pip', label: 'Installing pip' },
  { key: 'packages', label: 'Installing packages' },
] as const

function stepIndex(step: string): number {
  return STEPS.findIndex((item) => item.key === step)
}

function InstallingPanel({ progress }: { progress: SetupProgress | null }): JSX.Element {
  const currentIdx = progress ? stepIndex(progress.step) : -1
  const percent = progress?.percent ?? 0

  return (
    <Card className="w-80 space-y-4 p-6 shadow-none" role="status" aria-live="polite">
      <p className="text-sm font-medium text-foreground">Setting up environment…</p>

      <div className="flex gap-2">
        {STEPS.map((step, index) => {
          const done = index < currentIdx
          const active = index === currentIdx
          return (
            <div key={step.key} className="min-w-0 flex-1">
              <div className={`h-1 rounded-full ${done || active ? 'bg-primary' : 'bg-muted'} ${active ? 'animate-pulse opacity-70' : ''}`} />
              <p className={`mt-1 truncate text-[11px] ${active ? 'text-foreground' : 'text-muted-foreground'}`}>
                {step.label}
              </p>
            </div>
          )
        })}
      </div>

      <ProgressTrack value={percent} />

      <div className="flex items-center justify-between gap-2">
        <p className="min-w-0 flex-1 truncate text-xs text-muted-foreground">
          {progress?.currentPackage ?? (currentIdx >= 0 ? STEPS[currentIdx]?.label : 'Initialising…')}
        </p>
        <p className="shrink-0 text-xs tabular-nums text-muted-foreground">{percent}%</p>
      </div>
    </Card>
  )
}

function StartingPanel(): JSX.Element {
  return (
    <Card className="w-80 space-y-4 p-6 shadow-none" role="status" aria-live="polite">
      <div className="space-y-1">
        <p className="text-sm font-medium text-foreground">Starting backend…</p>
        <p className="text-xs text-muted-foreground">Launching the local AI server</p>
      </div>
      <ProgressTrack value={40} pulse />
    </Card>
  )
}

function ErrorPanel({ message }: { message: string | null }): JSX.Element {
  const lines = (message ?? 'Check the console for details').split('\n')
  const isAntivirusHint = message?.includes('antivirus') ?? false

  return (
    <Card className="w-80 space-y-4 p-6 shadow-none">
      <div className="flex items-center gap-2.5">
        <span className="flex size-8 items-center justify-center rounded-full bg-destructive/10 text-destructive">
          <AlertTriangle className="size-4" />
        </span>
        <p className="text-sm font-medium text-foreground">Something went wrong</p>
      </div>

      <div className="max-h-48 space-y-1 overflow-y-auto rounded-md border border-border bg-muted/30 p-3 select-text">
        {lines.map((line, index) => line === ''
          ? <div key={index} className="h-1" />
          : <p key={index} className="break-all font-mono text-xs text-muted-foreground">{line}</p>)}
      </div>

      {isAntivirusHint && (
        <div className="rounded-md border border-amber-500/30 bg-amber-500/10 p-3">
          <p className="text-xs font-medium text-amber-300">Antivirus detected</p>
          <p className="mt-0.5 text-xs text-amber-200/70">
            Add the app folder to your antivirus exclusions, then click Retry.
          </p>
        </div>
      )}

      <Button type="button" className="w-full" onClick={() => window.location.reload()}>
        <RotateCcw className="mr-1.5 size-4" />
        Retry
      </Button>
    </Card>
  )
}

export default function FirstRunSetup(): JSX.Element {
  const { setupStatus, setupProgress, setupError, saveDataDir, defaultDataDir, backendStatus, backendError, platform, arch } = useAppStore()

  const renderPanel = (): JSX.Element => {
    switch (setupStatus) {
      case 'idle':
      case 'checking':
        return <CheckingPanel />
      case 'needed':
        return <ChoosePathPanel defaultPath={defaultDataDir} platform={platform} arch={arch} onConfirm={saveDataDir} />
      case 'installing':
        return <InstallingPanel progress={setupProgress} />
      case 'done':
        if (backendStatus === 'error') return <ErrorPanel message={backendError} />
        return <StartingPanel />
      case 'error':
        return <ErrorPanel message={setupError} />
      default:
        return <StartingPanel />
    }
  }

  return (
    <div className="flex h-full flex-col bg-background">
      <div className="flex h-9 shrink-0 items-center border-b border-border/50 px-3">
        <div className="flex items-center">
          <PolyKitWordmark className="text-xs" />
        </div>
      </div>

      <main className="flex flex-1 flex-col items-center justify-center p-6">
        <AppHeader />
        {renderPanel()}
      </main>
    </div>
  )
}

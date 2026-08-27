import { useEffect, useState } from 'react'
import { LoaderCircle } from 'lucide-react'

import {
  Button,
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@shared/components/ui'
import { useI18n } from '@shared/i18n'
import { SettingsCard, SettingsPathRow, SettingsRow, SettingsSection } from './SettingsLayout'

function MoveFolderDialog({
  title,
  currentDir,
  itemKind,
  itemCount,
  moveLabel,
  moveDesc,
  deleteLabel,
  deleteDesc,
  status,
  onCancel,
  onMove,
  onDelete,
}: {
  title: string
  currentDir: string
  itemKind: string
  itemCount: number
  moveLabel: string
  moveDesc: string
  deleteLabel: string
  deleteDesc: string
  status: 'idle' | 'busy' | 'error'
  onCancel: () => void
  onMove: () => void
  onDelete: () => void
}): JSX.Element {
  const { t } = useI18n()

  return (
    <Dialog open onOpenChange={(open) => { if (!open && status !== 'busy') onCancel() }}>
      <DialogContent className="sm:max-w-[440px]">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>
            {t('settings.existingStorageItems', { count: itemCount, kind: itemKind })}
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          <div className="rounded-md border border-divider bg-muted/40 px-3 py-2 font-mono text-[11px] text-muted-foreground">
            <p className="truncate" title={currentDir}>{currentDir}</p>
          </div>

          {status === 'error' && (
            <p className="text-xs text-destructive" role="alert">{t('settings.storageActionFailed')}</p>
          )}

          <div className="grid gap-2">
            <Button
              type="button"
              variant="outline"
              className="h-auto justify-start px-4 py-3 text-left"
              onClick={onMove}
              disabled={status === 'busy'}
            >
              <span>
                <span className="block text-xs font-semibold text-foreground">{moveLabel}</span>
                <span className="mt-0.5 block text-[11px] font-normal text-muted-foreground">{moveDesc}</span>
              </span>
            </Button>

            <Button
              type="button"
              variant="outline"
              className="h-auto justify-start border-destructive/30 px-4 py-3 text-left text-destructive hover:bg-destructive/10 hover:text-destructive"
              onClick={onDelete}
              disabled={status === 'busy'}
            >
              <span>
                <span className="block text-xs font-semibold">{deleteLabel}</span>
                <span className="mt-0.5 block text-[11px] font-normal text-muted-foreground">{deleteDesc}</span>
              </span>
            </Button>
          </div>
        </div>

        <DialogFooter>
          <Button type="button" variant="secondary" onClick={onCancel} disabled={status === 'busy'}>
            {status === 'busy' && <LoaderCircle className="mr-1.5 size-4 animate-spin" />}
            {status === 'busy' ? t('settings.pleaseWait') : t('common.cancel')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

export function StorageSection(): JSX.Element {
  const { t } = useI18n()
  const [serverManagedPaths] = useState(true)
  const [modelsDir, setModelsDir] = useState('')
  const [workspaceDir, setWorkspaceDir] = useState('')
  const [workflowsDir, setWorkflowsDir] = useState('')
  const [cacheStatus, setCacheStatus] = useState<'idle' | 'clearing' | 'done' | 'error'>('idle')

  const [pendingModelsDir, setPendingModelsDir] = useState<string | null>(null)
  const [existingModels, setExistingModels] = useState<string[]>([])
  const [modelsActionStatus, setModelsActionStatus] = useState<'idle' | 'busy' | 'error'>('idle')

  const [pendingWorkspaceDir, setPendingWorkspaceDir] = useState<string | null>(null)
  const [existingWorkspaces, setExistingWorkspaces] = useState<string[]>([])
  const [workspaceActionStatus, setWorkspaceActionStatus] = useState<'idle' | 'busy' | 'error'>('idle')

  const [pendingWorkflowsDir, setPendingWorkflowsDir] = useState<string | null>(null)
  const [existingWorkflows, setExistingWorkflows] = useState<string[]>([])
  const [workflowsActionStatus, setWorkflowsActionStatus] = useState<'idle' | 'busy' | 'error'>('idle')

  useEffect(() => {
    window.polykit.settings.get().then((settings) => {
      setModelsDir(settings.modelsDir)
      setWorkspaceDir(settings.workspaceDir)
      setWorkflowsDir(settings.workflowsDir)
    })
  }, [])

  async function applyModelsDir(path: string) {
    setModelsDir(path)
    await window.polykit.settings.set({ modelsDir: path })
    await window.polykit.api.updatePaths({ modelsDir: path })
  }

  async function applyWorkspaceDir(path: string) {
    setWorkspaceDir(path)
    await window.polykit.settings.set({ workspaceDir: path })
    await window.polykit.api.updatePaths({ workspaceDir: path })
  }

  async function applyWorkflowsDir(path: string) {
    setWorkflowsDir(path)
    await window.polykit.settings.set({ workflowsDir: path })
  }

  async function handleBrowseModels() {
    const newPath = await window.polykit.fs.selectDirectory(modelsDir)
    if (!newPath || newPath === modelsDir) return
    const models = await window.polykit.fs.listDir(modelsDir)
    if (models.length > 0) {
      setExistingModels(models)
      setPendingModelsDir(newPath)
      return
    }
    await applyModelsDir(newPath)
  }

  async function handleBrowseWorkspace() {
    const newPath = await window.polykit.fs.selectDirectory(workspaceDir)
    if (!newPath || newPath === workspaceDir) return
    const items = await window.polykit.fs.listDir(workspaceDir)
    if (items.length > 0) {
      setExistingWorkspaces(items)
      setPendingWorkspaceDir(newPath)
      return
    }
    await applyWorkspaceDir(newPath)
  }

  async function handleBrowseWorkflows() {
    const newPath = await window.polykit.fs.selectDirectory(workflowsDir)
    if (!newPath || newPath === workflowsDir) return
    const items = await window.polykit.fs.listDir(workflowsDir)
    if (items.length > 0) {
      setExistingWorkflows(items)
      setPendingWorkflowsDir(newPath)
      return
    }
    await applyWorkflowsDir(newPath)
  }

  function closeModelsDialog() {
    setPendingModelsDir(null)
    setExistingModels([])
    setModelsActionStatus('idle')
  }

  function closeWorkspaceDialog() {
    setPendingWorkspaceDir(null)
    setExistingWorkspaces([])
    setWorkspaceActionStatus('idle')
  }

  function closeWorkflowsDialog() {
    setPendingWorkflowsDir(null)
    setExistingWorkflows([])
    setWorkflowsActionStatus('idle')
  }

  async function handleMoveModels() {
    if (!pendingModelsDir) return
    setModelsActionStatus('busy')
    const result = await window.polykit.fs.moveDirectory({ src: modelsDir, dest: pendingModelsDir })
    if (result.success) {
      await applyModelsDir(pendingModelsDir)
      closeModelsDialog()
    } else {
      setModelsActionStatus('error')
    }
  }

  async function handleDeleteModels() {
    if (!pendingModelsDir) return
    setModelsActionStatus('busy')
    const result = await window.polykit.fs.deleteDirectory(modelsDir)
    if (result.success) {
      await applyModelsDir(pendingModelsDir)
      closeModelsDialog()
    } else {
      setModelsActionStatus('error')
    }
  }

  async function handleMoveWorkspace() {
    if (!pendingWorkspaceDir) return
    setWorkspaceActionStatus('busy')
    const result = await window.polykit.fs.moveDirectory({ src: workspaceDir, dest: pendingWorkspaceDir })
    if (result.success) {
      await applyWorkspaceDir(pendingWorkspaceDir)
      closeWorkspaceDialog()
    } else {
      setWorkspaceActionStatus('error')
    }
  }

  async function handleDeleteWorkspace() {
    if (!pendingWorkspaceDir) return
    setWorkspaceActionStatus('busy')
    const result = await window.polykit.fs.deleteDirectory(workspaceDir)
    if (result.success) {
      await applyWorkspaceDir(pendingWorkspaceDir)
      closeWorkspaceDialog()
    } else {
      setWorkspaceActionStatus('error')
    }
  }

  async function handleMoveWorkflows() {
    if (!pendingWorkflowsDir) return
    setWorkflowsActionStatus('busy')
    const result = await window.polykit.fs.moveDirectory({ src: workflowsDir, dest: pendingWorkflowsDir })
    if (result.success) {
      await applyWorkflowsDir(pendingWorkflowsDir)
      closeWorkflowsDialog()
    } else {
      setWorkflowsActionStatus('error')
    }
  }

  async function handleDeleteWorkflows() {
    if (!pendingWorkflowsDir) return
    setWorkflowsActionStatus('busy')
    const result = await window.polykit.fs.deleteDirectory(workflowsDir)
    if (result.success) {
      await applyWorkflowsDir(pendingWorkflowsDir)
      closeWorkflowsDialog()
    } else {
      setWorkflowsActionStatus('error')
    }
  }

  async function handleClearCache() {
    setCacheStatus('clearing')
    const result = await window.polykit.cache.clear()
    if (!result.success) console.error('[cache:clear] renderer:', result.error)
    setCacheStatus(result.success ? 'done' : 'error')
    setTimeout(() => setCacheStatus('idle'), 2500)
  }

  return (
    <SettingsSection title={t('settings.storage')} subtitle={t('settings.storageSubtitle')}>
      <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        <SettingsCard
          title={t('settings.directories')}
          description={serverManagedPaths ? t('settings.serverOwnsDirectories') : t('settings.directoriesDescription')}
        >
          <SettingsPathRow
            label={t('nodePacks.models')}
            description={t('settings.modelsDirectoryDescription')}
            value={modelsDir}
            onBrowse={serverManagedPaths ? undefined : handleBrowseModels}
          />
          <SettingsPathRow
            label={t('settings.workspace')}
            description={t('settings.workspaceDirectoryDescription')}
            value={workspaceDir}
            onBrowse={serverManagedPaths ? undefined : handleBrowseWorkspace}
          />
          <SettingsPathRow
            label={t('nav.workflows')}
            description={t('settings.workflowsDirectoryDescription')}
            value={workflowsDir}
            onBrowse={serverManagedPaths ? undefined : handleBrowseWorkflows}
          />
        </SettingsCard>

        <SettingsCard
          title={t('settings.cache')}
          description={serverManagedPaths ? t('settings.serverManagedCache') : t('settings.cacheDescription')}
        >
          <SettingsRow label={t('settings.tempFiles')} description={t('settings.tempFilesDescription')}>
            {serverManagedPaths ? (
              <span className="text-[11px] text-muted-foreground">{t('settings.serverManaged')}</span>
            ) : (
              <Button
                type="button"
                variant={cacheStatus === 'error' ? 'destructive' : 'secondary'}
                size="sm"
                onClick={() => { void handleClearCache() }}
                disabled={cacheStatus === 'clearing'}
                className={cacheStatus === 'done' ? 'border border-emerald-500/30 bg-emerald-500/10 text-emerald-300 hover:bg-emerald-500/15' : undefined}
              >
                {cacheStatus === 'clearing' && <LoaderCircle className="mr-1.5 size-3.5 animate-spin" />}
                {cacheStatus === 'clearing' ? t('settings.clearing') : cacheStatus === 'done' ? t('settings.cleared') : cacheStatus === 'error' ? t('settings.failed') : t('settings.clearCache')}
              </Button>
            )}
          </SettingsRow>
        </SettingsCard>
      </div>

      {pendingModelsDir && (
        <MoveFolderDialog
          title={t('settings.changeModelsFolder')}
          currentDir={modelsDir}
          itemKind={t('settings.modelFiles')}
          itemCount={existingModels.length}
          moveLabel={t('settings.moveToNewFolder')}
          moveDesc={t('settings.moveModelsDescription')}
          deleteLabel={t('settings.deleteModels')}
          deleteDesc={t('settings.deleteModelsDescription')}
          status={modelsActionStatus}
          onCancel={closeModelsDialog}
          onMove={() => { void handleMoveModels() }}
          onDelete={() => { void handleDeleteModels() }}
        />
      )}

      {pendingWorkspaceDir && (
        <MoveFolderDialog
          title={t('settings.changeWorkspaceFolder')}
          currentDir={workspaceDir}
          itemKind={t('settings.workspaceItems')}
          itemCount={existingWorkspaces.length}
          moveLabel={t('settings.moveToNewFolder')}
          moveDesc={t('settings.moveWorkspaceDescription')}
          deleteLabel={t('settings.deleteWorkspaceFiles')}
          deleteDesc={t('settings.deleteWorkspaceDescription')}
          status={workspaceActionStatus}
          onCancel={closeWorkspaceDialog}
          onMove={() => { void handleMoveWorkspace() }}
          onDelete={() => { void handleDeleteWorkspace() }}
        />
      )}

      {pendingWorkflowsDir && (
        <MoveFolderDialog
          title={t('settings.changeWorkflowsFolder')}
          currentDir={workflowsDir}
          itemKind={t('settings.workflowFiles')}
          itemCount={existingWorkflows.length}
          moveLabel={t('settings.moveToNewFolder')}
          moveDesc={t('settings.moveWorkflowsDescription')}
          deleteLabel={t('settings.deleteWorkflows')}
          deleteDesc={t('settings.deleteWorkflowsDescription')}
          status={workflowsActionStatus}
          onCancel={closeWorkflowsDialog}
          onMove={() => { void handleMoveWorkflows() }}
          onDelete={() => { void handleDeleteWorkflows() }}
        />
      )}
    </SettingsSection>
  )
}

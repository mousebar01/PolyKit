import { useState } from 'react'

import { Button } from '@shared/components/ui/button'
import { useI18n } from '@shared/i18n'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@shared/components/ui/dialog'
import { Input } from '@shared/components/ui/input'
import { Label } from '@shared/components/ui/label'

export function AssetRenameDialog({ currentName, onConfirm, onCancel }: {
  currentName: string
  onConfirm: (newName: string) => void
  onCancel: () => void
}): JSX.Element {
  const { t } = useI18n()
  const [value, setValue] = useState(currentName)

  const submit = (): void => {
    const trimmed = value.trim()
    if (trimmed && trimmed !== currentName) onConfirm(trimmed)
    else onCancel()
  }

  return (
    <Dialog open onOpenChange={(open) => { if (!open) onCancel() }}>
      <DialogContent className="max-w-sm">
        <DialogHeader>
          <DialogTitle>{t('assets.renameTitle')}</DialogTitle>
          <DialogDescription>{t('assets.renameDescription')}</DialogDescription>
        </DialogHeader>
        <div className="space-y-2">
          <Label htmlFor="asset-rename-input">{t('assets.newName')}</Label>
          <Input
            id="asset-rename-input"
            autoFocus
            value={value}
            onChange={(event) => setValue(event.target.value)}
            onKeyDown={(event) => { if (event.key === 'Enter') submit() }}
          />
        </div>
        <DialogFooter>
          <Button type="button" variant="secondary" onClick={onCancel}>{t('common.cancel')}</Button>
          <Button type="button" onClick={submit} disabled={!value.trim()}>{t('assets.rename')}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

export function AssetDeleteDialog({ count, displayName, onConfirm, onCancel }: {
  count: number
  displayName?: string
  onConfirm: () => void
  onCancel: () => void
}): JSX.Element {
  const { t } = useI18n()
  const targetName = displayName ?? t('assets.thisAsset')

  return (
    <Dialog open onOpenChange={(open) => { if (!open) onCancel() }}>
      <DialogContent className="max-w-sm">
        <DialogHeader>
          <DialogTitle>{t('assets.deleteTitle')}</DialogTitle>
          <DialogDescription>
            {count === 1
              ? t('assets.deleteDescriptionSingle', { name: targetName })
              : t('assets.deleteDescriptionMultiple', { count })}
          </DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <Button type="button" variant="secondary" onClick={onCancel}>{t('common.cancel')}</Button>
          <Button type="button" variant="destructive" onClick={onConfirm}>{t('assets.delete')}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

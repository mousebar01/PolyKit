import type { ReactNode } from 'react'
import { Aperture, Box, Camera, Grid3X3, MoveHorizontal, RotateCw, Scan } from 'lucide-react'

import { Button, Card, Slider } from '@shared/components/ui'
import { useI18n, type TranslationKey } from '@shared/i18n'
import type { ViewMode } from '../models'
export type { ViewMode }

interface ViewerToolbarProps {
  viewMode: ViewMode
  autoRotate: boolean
  onViewMode: (mode: ViewMode) => void
  onAutoRotate: () => void
  onScreenshot: () => void
  showViewModes?: boolean
  /** Inspection-only spread of multipart models; shown when the model has parts. */
  canSeparate?: boolean
  separationOpen?: boolean
  onToggleSeparation?: () => void
}

const MODES: { mode: ViewMode; icon: ReactNode; labelKey: TranslationKey }[] = [
  { mode: 'solid', labelKey: 'assets.viewSolid', icon: <Box className="h-4 w-4" /> },
  { mode: 'wireframe', labelKey: 'assets.viewWireframe', icon: <Grid3X3 className="h-4 w-4" /> },
  { mode: 'normals', labelKey: 'assets.viewNormals', icon: <Scan className="h-4 w-4" /> },
  { mode: 'matcap', labelKey: 'assets.viewMatcap', icon: <Aperture className="h-4 w-4" /> },
  { mode: 'uv', labelKey: 'assets.viewUvChecker', icon: <Grid3X3 className="h-4 w-4" /> },
]

export function ViewerToolbar({
  viewMode,
  autoRotate,
  onViewMode,
  onAutoRotate,
  onScreenshot,
  showViewModes = true,
  canSeparate = false,
  separationOpen = false,
  onToggleSeparation,
}: ViewerToolbarProps): JSX.Element {
  const { t } = useI18n()
  const hasSeparation = canSeparate && onToggleSeparation !== undefined

  return (
    <div className="absolute left-4 top-1/2 z-20 flex -translate-y-1/2 flex-col items-center gap-1.5 rounded-lg border border-divider bg-card/95 p-2 backdrop-blur-sm">
      {showViewModes && MODES.map(({ mode, icon, labelKey }) => (
        <ToolbarButton
          key={mode}
          active={viewMode === mode}
          label={t(labelKey)}
          onClick={() => onViewMode(mode)}
        >
          {icon}
        </ToolbarButton>
      ))}

      {showViewModes && <div className="my-1 border-t border-divider" aria-hidden="true" />}

      <ToolbarButton active={autoRotate} label={t('assets.autoRotate')} onClick={onAutoRotate}>
        <RotateCw className="h-4 w-4" />
      </ToolbarButton>

      <ToolbarButton active={false} label={t('assets.screenshot')} onClick={onScreenshot}>
        <Camera className="h-4 w-4" />
      </ToolbarButton>

      {hasSeparation && (
        <>
          <div className="my-1 w-full border-t border-divider" aria-hidden="true" />
          <ToolbarButton
            active={separationOpen}
            expanded={separationOpen}
            controls="viewer-separation-control"
            label={t('assets.partSeparation')}
            onClick={() => onToggleSeparation?.()}
          >
            <MoveHorizontal className="h-4 w-4" />
          </ToolbarButton>
        </>
      )}
    </div>
  )
}

interface ViewerSeparationControlProps {
  separation: number
  onSeparation: (value: number) => void
}

export function ViewerSeparationControl({ separation, onSeparation }: ViewerSeparationControlProps): JSX.Element {
  const { t } = useI18n()

  return (
    <div id="viewer-separation-control" className="pointer-events-auto absolute bottom-8 left-1/2 z-20 w-[calc(100%-2rem)] max-w-md -translate-x-1/2 animate-slide-up">
      <Card className="border-primary/25 bg-card/95 px-5 py-4 backdrop-blur-md">
        <div className="mb-2 flex items-center justify-between gap-3">
          <span className="text-xs font-medium text-foreground">{t('assets.partSeparation')}</span>
          <span className="font-mono text-xs tabular-nums text-primary">{separation.toFixed(2)}</span>
        </div>
        <Slider
          min={0}
          max={2}
          step={0.05}
          value={[separation]}
          onValueChange={([next]) => onSeparation(Number(next.toFixed(2)))}
          aria-label={t('assets.partSeparation')}
          className="nodrag h-8 w-full"
        />
      </Card>
    </div>
  )
}

interface ToolbarButtonProps {
  active: boolean
  expanded?: boolean
  controls?: string
  label: string
  onClick: () => void
  children: ReactNode
}

function ToolbarButton({ active, expanded, controls, label, onClick, children }: ToolbarButtonProps): JSX.Element {
  return (
    <Button
      type="button"
      variant={active ? 'default' : 'ghost'}
      size="icon"
      className="h-8 w-8"
      title={label}
      aria-label={label}
      aria-pressed={active}
      aria-expanded={expanded}
      aria-controls={controls}
      onClick={onClick}
    >
      {children}
    </Button>
  )
}

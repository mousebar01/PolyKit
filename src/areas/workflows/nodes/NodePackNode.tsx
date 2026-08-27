import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { Handle, Position, useReactFlow } from '@xyflow/react'
import { ArrowRight, FolderOpen } from 'lucide-react'

import { Button, Input, Label, Select, SelectContent, SelectItem, SelectTrigger, SelectValue, Switch } from '@shared/components/ui'
import { useNodePacksStore } from '@shared/stores/nodePacksStore'
import type { WFNodeData } from '@shared/types/runtime.d'
import { buildAllWorkflowNodePacks } from '../mockNodePacks'
import type { ParamSchema } from '../mockNodePacks'
import { useWorkflowRunStore } from '../workflowRunStore'
import BaseNode from './BaseNode'

// ─── Handle colors ────────────────────────────────────────────────────────────
// These encode workflow data types on the canvas and are an explicit DESIGN.md
// exception to ordinary app-surface semantic colors.
const HANDLE_COLOR: Record<string, string> = {
  audio: '#5680b8',
  image: '#5680b8',
  mesh: '#5d94d9',
  text: '#fbbf24',
}

const TAG_CLS: Record<string, string> = {
  audio: 'border-sky-500/30 bg-sky-500/10 text-sky-400',
  image: 'border-sky-500/30 bg-sky-500/10 text-sky-400',
  mesh: 'border-primary/30 bg-primary/10 text-primary',
  text: 'border-amber-500/30 bg-amber-500/10 text-amber-400',
}

// ─── Param controls ───────────────────────────────────────────────────────────

// `nodrag` prevents React Flow from treating interaction with a form control as
// a request to drag the containing node.
const inputCls = 'nodrag h-7 px-2 py-1 text-[11px]'

function decimalPlaces(value: number): number {
  if (!Number.isFinite(value)) return 0
  const text = value.toString().toLowerCase()
  const [mantissa, exponentText] = text.split('e')
  const fractionalPlaces = mantissa.split('.')[1]?.length ?? 0
  const exponent = exponentText ? Number(exponentText) : 0
  return Math.max(0, fractionalPlaces - exponent)
}

function snapFloat(value: number, min: number | undefined, max: number | undefined, step: number | undefined): number {
  const bounded = Math.min(max ?? value, Math.max(min ?? value, value))
  if (!step || !Number.isFinite(step) || step <= 0) return bounded

  const origin = min ?? 0
  const snapped = origin + Math.round((bounded - origin) / step) * step
  const precision = Math.min(12, Math.max(decimalPlaces(step), decimalPlaces(origin), decimalPlaces(max ?? 0)))
  return Math.min(max ?? Infinity, Math.max(min ?? -Infinity, Number(snapped.toFixed(precision))))
}

function IntInput({ id, label, value, onChange }: { id: string; label: string; value: number; onChange: (value: number) => void }) {
  const [text, setText] = useState(String(value))
  const prevValue = useRef(value)
  if (prevValue.current !== value && parseInt(text, 10) !== value) {
    prevValue.current = value
    setText(String(value))
  }
  return (
    <Input
      id={id}
      aria-label={label}
      type="text"
      inputMode="numeric"
      value={text}
      onChange={(event) => {
        const raw = event.target.value
        if (raw !== '' && raw !== '-' && !/^-?\d+$/.test(raw)) return
        setText(raw)
        const parsed = parseInt(raw, 10)
        if (!isNaN(parsed)) {
          prevValue.current = parsed
          onChange(parsed)
        }
      }}
      className={inputCls}
    />
  )
}

function FloatInput({ id, label, value, min, max, step, onChange }: {
  id: string
  label: string
  value: number
  min?: number
  max?: number
  step?: number
  onChange: (value: number) => void
}) {
  const normalizedValue = snapFloat(value, min, max, step)
  const [text, setText] = useState(String(normalizedValue))
  useEffect(() => {
    setText((current) => parseFloat(current.replace(',', '.')) === normalizedValue ? current : String(normalizedValue))
  }, [normalizedValue])
  const commit = (raw: string) => {
    const parsed = parseFloat(raw.replace(',', '.'))
    if (!Number.isFinite(parsed)) return
    const next = snapFloat(parsed, min, max, step)
    setText(String(next))
    onChange(next)
  }
  return (
    <Input
      id={id}
      aria-label={label}
      type="text"
      inputMode="decimal"
      value={text}
      onChange={(event) => {
        const raw = event.target.value.replace(',', '.')
        if (raw !== '' && raw !== '-' && raw !== '.' && !/^-?\d*\.?\d*$/.test(raw)) return
        setText(event.target.value)
        const parsed = parseFloat(raw)
        if (!isNaN(parsed)) {
          const next = snapFloat(parsed, min, max, step)
          onChange(next)
        }
      }}
      onBlur={() => commit(text)}
      min={min}
      max={max}
      step={step}
      className={inputCls}
    />
  )
}

function FileSelectControl({ id, label, param, value, dirValue, onChange }: {
  id: string
  label: string
  param: ParamSchema
  value: string
  dirValue: string
  onChange: (value: string) => void
}) {
  const [files, setFiles] = useState<string[]>([])
  const extsKey = (param.extensions ?? []).join(',')

  useEffect(() => {
    let alive = true
    if (!dirValue) {
      setFiles([])
      return
    }
    window.polykit.fs.listFiles(dirValue, param.extensions ?? undefined).then((list) => {
      if (alive) setFiles(list)
    }).catch(() => {
      if (alive) setFiles([])
    })
    return () => { alive = false }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- extension list represented by extsKey
  }, [dirValue, extsKey])

  const placeholder = !dirValue ? 'Pick a folder first…' : files.length === 0 ? 'No files found' : 'Select…'
  const options = value && !files.includes(value) ? [value, ...files] : files

  return (
    <Select value={value || undefined} disabled={!dirValue} onValueChange={onChange}>
      <SelectTrigger id={id} aria-label={label} className="nodrag h-7 text-[11px]">
        <SelectValue placeholder={placeholder} />
      </SelectTrigger>
      <SelectContent>
        {options.map((file) => (
          <SelectItem key={file} value={file}>
            {file === value && !files.includes(file) ? `${file} (missing)` : file}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  )
}

function ParamControl({ id, param, value, onChange, resolvedParams }: {
  id: string
  param: ParamSchema
  value: number | string | boolean
  onChange: (value: number | string | boolean) => void
  resolvedParams: Record<string, unknown>
}) {
  if (param.type === 'file-select') {
    const dirValue = String(resolvedParams[param.dir_from ?? ''] ?? '')
    return (
      <FileSelectControl
        id={id}
        label={param.label}
        param={param}
        value={String(value ?? '')}
        dirValue={dirValue}
        onChange={onChange}
      />
    )
  }

  if (param.type === 'select') {
    const selectedValue = String(value ?? '')
    return (
      <Select
        value={selectedValue}
        onValueChange={(next) => {
          const option = param.options?.find((item) => String(item.value) === next)
          onChange(option?.value ?? next)
        }}
      >
        <SelectTrigger id={id} aria-label={param.label} className="nodrag h-7 text-[11px]">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {param.options?.map((option) => (
            <SelectItem key={String(option.value)} value={String(option.value)}>{option.label ?? String(option.value)}</SelectItem>
          ))}
        </SelectContent>
      </Select>
    )
  }

  if (param.type === 'string') {
    return (
      <div className="flex items-center gap-1">
        <Input
          id={id}
          aria-label={param.label}
          type="text"
          value={value as string}
          placeholder={param.tooltip ?? ''}
          onChange={(event) => onChange(event.target.value)}
          className={`${inputCls} flex-1`}
        />
        <Button
          type="button"
          variant="outline"
          size="icon"
          className="nodrag h-7 w-7 shrink-0"
          onClick={async () => {
            const path = await window.polykit.fs.selectDirectory()
            if (path) onChange(path)
          }}
          title={`Choose folder for ${param.label}`}
          aria-label={`Choose folder for ${param.label}`}
        >
          <FolderOpen className="h-3.5 w-3.5" />
        </Button>
      </div>
    )
  }

  if (param.type === 'boolean') {
    return (
      <Switch
        id={id}
        checked={Boolean(value)}
        onCheckedChange={onChange}
        aria-label={param.label}
        title={param.tooltip}
        className="nodrag"
      />
    )
  }

  if (param.type === 'float') {
    return <FloatInput id={id} label={param.label} value={value as number} min={param.min} max={param.max} step={param.step} onChange={onChange} />
  }

  return <IntInput id={id} label={param.label} value={value as number} onChange={onChange} />
}

// ─── NodePackNode ────────────────────────────────────────────────────────────

export default function NodePackNode({ id, data, selected }: { id: string; data: WFNodeData; selected?: boolean }) {
  const { updateNodeData } = useReactFlow()
  const running = useWorkflowRunStore((state) => state.activeNodeId === id)

  const ioRowRef = useRef<HTMLDivElement>(null)
  const ioRow2Ref = useRef<HTMLDivElement>(null)
  const [handleTop, setHandleTop] = useState('50%')
  const [handle2Top, setHandle2Top] = useState('50%')

  const { modelNodePacks, processNodePacks } = useNodePacksStore()
  const allNodePacks = buildAllWorkflowNodePacks(modelNodePacks, processNodePacks)
  const ext = allNodePacks.find((item) => item.id === data.nodePackId)

  const inputs = ext?.inputs
  const isMulti = inputs && inputs.length > 1
  const isTerminal = ext?.id === 'mesh-exporter'
  const outputColor = HANDLE_COLOR[ext?.output ?? 'mesh']

  useLayoutEffect(() => {
    if (ioRowRef.current) {
      const center = ioRowRef.current.offsetTop + ioRowRef.current.offsetHeight / 2
      setHandleTop(`${center}px`)
    }
    if (ioRow2Ref.current) {
      const center = ioRow2Ref.current.offsetTop + ioRow2Ref.current.offsetHeight / 2
      setHandle2Top(`${center}px`)
    }
  }, [isMulti])

  const patchParam = useCallback((key: string, val: number | string | boolean) => {
    const params = { ...data.params, [key]: val }
    updateNodeData(id, { params })
  }, [id, data.params, updateNodeData])

  const paramById = new Map(ext?.params.map((param) => [param.id, param]))

  const isVisible = (param: ParamSchema): boolean => {
    if (!param.show_if) return true
    return Object.entries(param.show_if).every(([key, expected]) => {
      const current = data.params[key] ?? paramById.get(key)?.default
      return Array.isArray(expected) ? expected.includes(current as string | number) : current === expected
    })
  }

  const visibleParams = (ext?.params ?? []).filter(isVisible)

  const ioSubheader = isMulti ? (
    <div className="flex flex-col divide-y divide-border/40">
      <div ref={ioRowRef} className="flex items-center justify-between px-3 py-2">
        <span className={`inline-flex items-center rounded border px-1.5 py-0.5 text-[9px] font-medium ${TAG_CLS[inputs[0]] ?? 'border-border bg-muted text-muted-foreground'}`}>
          {ext?.inputLabels?.[0] ?? inputs[0]}
        </span>
        {!isTerminal && (
          <>
            <ArrowRight className="h-2.5 w-2.5 shrink-0 text-muted-foreground" />
            <span className={`inline-flex items-center rounded border px-1.5 py-0.5 text-[9px] font-medium ${TAG_CLS[ext?.output ?? ''] ?? 'border-border bg-muted text-muted-foreground'}`}>
              {ext?.output ?? '—'}
            </span>
          </>
        )}
      </div>
      <div ref={ioRow2Ref} className="flex items-center px-3 py-2">
        <span className={`inline-flex items-center rounded border px-1.5 py-0.5 text-[9px] font-medium ${TAG_CLS[inputs[1]] ?? 'border-border bg-muted text-muted-foreground'}`}>
          {ext?.inputLabels?.[1] ?? inputs[1]}
        </span>
      </div>
    </div>
  ) : (
    <div ref={ioRowRef} className="flex items-center justify-between px-3 py-2">
      <span className={`inline-flex items-center rounded border px-1.5 py-0.5 text-[9px] font-medium ${TAG_CLS[ext?.input ?? ''] ?? 'border-border bg-muted text-muted-foreground'}`}>
        {ext?.input ?? '—'}
      </span>
      {!isTerminal && (
        <>
          <ArrowRight className="h-2.5 w-2.5 shrink-0 text-muted-foreground" />
          <span className={`inline-flex items-center rounded border px-1.5 py-0.5 text-[9px] font-medium ${TAG_CLS[ext?.output ?? ''] ?? 'border-border bg-muted text-muted-foreground'}`}>
            {ext?.output ?? '—'}
          </span>
        </>
      )}
    </div>
  )

  const handlesEl = (
    <>
      <Handle
        id="input-0"
        type="target"
        position={Position.Left}
        style={{ background: HANDLE_COLOR[isMulti ? inputs[0] : (ext?.input ?? 'image')], width: 14, height: 14, border: '2.5px solid #18181b', top: handleTop }}
      />
      {isMulti && (
        <Handle
          id="input-1"
          type="target"
          position={Position.Left}
          style={{ background: HANDLE_COLOR[inputs[1]], width: 14, height: 14, border: '2.5px solid #18181b', top: handle2Top }}
        />
      )}
      {!isTerminal && (
        <Handle
          id="output"
          type="source"
          position={Position.Right}
          style={{ background: outputColor, width: 14, height: 14, border: '2.5px solid #18181b', top: handleTop }}
        />
      )}
    </>
  )

  return (
    <BaseNode
      id={id}
      selected={selected}
      running={running}
      title={ext?.name ?? data.nodePackId ?? 'Unknown extension'}
      enabled={data.enabled}
      showInGenerate={data.showInGenerate ?? false}
      collapsible={visibleParams.length > 0}
      minWidth={200}
      subheader={ioSubheader}
      handles={handlesEl}
    >
      {visibleParams.length > 0 && (
        <div className="flex flex-col gap-2 px-3 pb-3 pt-2.5">
          {(() => {
            const resolvedParams = Object.fromEntries(
              (ext?.params ?? []).map((param) => [param.id, data.params[param.id] ?? param.default]),
            )
            return visibleParams.map((param) => {
              const value = (data.params[param.id] ?? param.default) as number | string | boolean
              const controlId = `${id}-param-${param.id}`
              return (
                <div key={param.id} className="flex items-center gap-2">
                  <Label htmlFor={controlId} className="w-24 shrink-0 text-[10px] leading-tight text-muted-foreground">{param.label}</Label>
                  <div className="min-w-0 flex-1">
                    <ParamControl
                      id={controlId}
                      param={param}
                      value={value}
                      onChange={(next) => patchParam(param.id, next)}
                      resolvedParams={resolvedParams}
                    />
                  </div>
                </div>
              )
            })
          })()}
        </div>
      )}
    </BaseNode>
  )
}

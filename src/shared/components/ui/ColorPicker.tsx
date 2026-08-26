import { useRef } from 'react'

import { cn } from '@shared/lib/utils'

interface ColorPickerProps {
  value: string
  onChange: (color: string) => void
  size?: 'sm' | 'md'
  label?: string
  className?: string
}

export function ColorPicker({ value, onChange, size = 'sm', label = 'Choose color', className }: ColorPickerProps): JSX.Element {
  const inputRef = useRef<HTMLInputElement>(null)
  const dim = size === 'sm' ? 'h-5 w-5' : 'h-6 w-6'

  return (
    <button
      type="button"
      onClick={() => inputRef.current?.click()}
      className={cn(
        'relative shrink-0 overflow-hidden rounded border border-border transition-colors hover:border-foreground/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background',
        dim,
        className,
      )}
      style={{ backgroundColor: value }}
      title={`${label}: ${value}`}
      aria-label={`${label}: ${value}`}
    >
      {/* Transparency checkerboard is a domain-specific swatch visualization. */}
      <span
        className="absolute inset-0 -z-10"
        style={{
          backgroundImage:
            'linear-gradient(45deg, #555 25%, transparent 25%),' +
            'linear-gradient(-45deg, #555 25%, transparent 25%),' +
            'linear-gradient(45deg, transparent 75%, #555 75%),' +
            'linear-gradient(-45deg, transparent 75%, #555 75%)',
          backgroundSize: '6px 6px',
          backgroundPosition: '0 0, 0 3px, 3px -3px, -3px 0',
        }}
        aria-hidden="true"
      />
      <input
        ref={inputRef}
        type="color"
        value={value}
        onChange={(event) => onChange(event.target.value)}
        className="pointer-events-none absolute h-0 w-0 opacity-0"
        tabIndex={-1}
        aria-hidden="true"
      />
    </button>
  )
}

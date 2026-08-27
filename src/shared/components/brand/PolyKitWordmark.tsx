/**
 * PolyKit wordmark — the product name as the logo.
 *
 * Two-tone lowercase wordmark: neutral "poly" + cool-blue italic "kit".
 * The color + weight + italic contrast gives it a designed, product feel
 * without any icon, gradient, or loud effects. The italic half is the
 * accent, so the mark stays understated at small sizes.
 */
export function PolyKitWordmark({
  className = '',
  kitClassName = '',
}: {
  className?: string
  kitClassName?: string
}): JSX.Element {
  return (
    <span className={`inline-flex items-baseline font-brand tracking-tight whitespace-nowrap ${className}`}>
      <span className="font-medium text-foreground">poly</span>
      <span className={`font-semibold italic text-primary ${kitClassName}`}>kit</span>
    </span>
  )
}

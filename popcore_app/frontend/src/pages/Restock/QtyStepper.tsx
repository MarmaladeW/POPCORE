import { useEffect, useState } from 'react'
import Icon from '../../components/pc/Icon'

// A quantity for thumbs: − and + are 44px, the number takes digits only and opens the number pad.
// A typed number outside min–max is shown as typed (marked) so the screen can refuse it; leaving
// the field brings it back into range.
export default function QtyStepper({ value, onChange, label, unit, min = 0, max = 999, disabled }: {
  value: number; onChange: (value: number) => void; label: string; unit?: string; min?: number; max?: number; disabled?: boolean
}) {
  const [text, setText] = useState(String(value))
  const [editing, setEditing] = useState(false)
  useEffect(() => { if (!editing) setText(String(value)) }, [value, editing])
  const clamp = (next: number) => Math.max(min, Math.min(max, next))
  const outside = value < min || value > max
  return <span className={`pc-qty${value > 0 ? ' is-set' : ''}${outside ? ' is-outside' : ''}`}>
    <button type="button" className="pc-qty-step" aria-label={`减少 ${label}`} disabled={disabled || value <= min}
      onClick={() => onChange(clamp(value - 1))}><Icon name="minus" size={16} /></button>
    <input className="pc-qty-input" type="text" inputMode="numeric" pattern="[0-9]*" autoComplete="off" aria-label={label}
      aria-invalid={outside || undefined} value={text} disabled={disabled}
      onFocus={event => { setEditing(true); event.target.select() }}
      onBlur={() => { setEditing(false); if (outside) onChange(clamp(value)); else setText(String(value)) }}
      onChange={event => {
        const digits = event.target.value.replace(/\D/g, '').slice(0, Math.max(3, String(max).length))
        setText(digits)
        onChange(digits === '' ? min : Number(digits))
      }} />
    <button type="button" className="pc-qty-step" aria-label={`增加 ${label}`} disabled={disabled || value >= max}
      onClick={() => onChange(clamp(value + 1))}><Icon name="plus" size={16} /></button>
    {unit && <span className="pc-qty-unit">{unit}</span>}
  </span>
}

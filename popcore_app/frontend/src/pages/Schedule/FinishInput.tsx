import { useEffect, useState } from 'react'
import { Input } from 'antd'
import { finishDigits, INVALID_FINISH, parseFinishDigits } from './finishTime'

// 实际下班 field: digits only with the numeric keypad; the form receives HH:mm (or INVALID_FINISH while
// the digits are not a time yet, which validation refuses).
export default function FinishInput({ value, onChange, id }: { value?: string; onChange?: (value: string) => void; id?: string }) {
  const [text, setText] = useState(() => finishDigits(value))
  useEffect(() => {
    // Follow outside changes (form reset, another shift) without fighting the user's typing.
    if (value !== INVALID_FINISH && parseFinishDigits(text) !== (value || '')) setText(finishDigits(value))
  }, [value]) // eslint-disable-line react-hooks/exhaustive-deps
  const parsed = parseFinishDigits(text)
  return <Input
    id={id}
    aria-label="实际下班 Actual finish"
    inputMode="numeric"
    pattern="[0-9]*"
    autoComplete="off"
    placeholder="2134"
    value={text}
    // Always render the suffix: antd rebuilds the input when a suffix appears or disappears, dropping focus mid-typing.
    suffix={<span className="pc-finish-read">{parsed ? `= ${parsed}` : ''}</span>}
    onChange={event => {
      const next = event.target.value.replace(/\D/g, '').slice(0, 4)
      setText(next)
      const time = parseFinishDigits(next)
      onChange?.(time === null ? INVALID_FINISH : time)
    }}
  />
}

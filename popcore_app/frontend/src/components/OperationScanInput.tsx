import { Input } from 'antd'
import { useState } from 'react'

export default function OperationScanInput({
  onScan, disabled = false,
}: {
  onScan: (code: string) => void | Promise<void>
  disabled?: boolean
}) {
  const [code, setCode] = useState('')

  return (
    <Input
      value={code}
      disabled={disabled}
      autoComplete="off"
      placeholder="Scan barcode, then press Enter"
      aria-label="Scan barcode"
      onChange={event => setCode(event.target.value)}
      onPressEnter={event => {
        if (event.nativeEvent.isComposing) return
        const scanned = code.replace(/[\r\n]+$/g, '')
        if (!scanned) return
        void onScan(scanned)
        setCode('')
      }}
    />
  )
}

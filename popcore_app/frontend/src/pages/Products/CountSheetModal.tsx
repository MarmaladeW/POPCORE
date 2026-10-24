import { useEffect, useState } from 'react'
import { Modal, Select, Space, Alert, Typography } from 'antd'
import client from '../../api/client'

const { Text } = Typography

interface Props {
  open: boolean
  storeCode?: string
  onClose: () => void
}

type Scope = 'legacy' | 'catalog'

// Every location the opening count needs a sheet for (scripts/inventory_opening.py counts them all).
const LOCATIONS = [
  { value: 'DT:floor',     label: 'DT · Floor' },
  { value: 'DT:upstairs',  label: 'DT · Upstairs' },
  { value: 'DT:claw',      label: 'DT · Claw machine' },
  { value: 'DT:display',   label: 'DT · Display' },
  { value: 'MK:floor',     label: 'MK · Floor' },
  { value: 'MK:warehouse', label: 'MK · Warehouse' },
  { value: 'MK:display',   label: 'MK · Display' },
]

const ERROR_HINTS: Record<string, (store: string) => string> = {
  location_unknown: store => `This ${store} location is not an active inventory location. Ask an admin to check the location setup.`,
  invalid_scope: () => 'Choose legacy stock or the whole catalog, then retry.',
  inventory_access_denied: store => `Your account has no inventory access to ${store}. An admin must grant it before you can download this sheet.`,
}

interface SheetError { message: string; hint?: string }

// Blob downloads return the JSON error body as a Blob, so the client interceptor cannot read it.
async function readSheetError(err: any, store: string): Promise<SheetError> {
  const data = err?.response?.data
  let body: { error?: string; code?: string } | undefined
  if (data instanceof Blob) {
    try { body = JSON.parse(await data.text()) } catch { body = undefined }
  } else if (data && typeof data === 'object') {
    body = data
  }
  if (body?.error) return { message: body.error, hint: body.code ? ERROR_HINTS[body.code]?.(store) : undefined }
  return { message: 'Count sheet download failed — please try again' }
}

export default function CountSheetModal({ open, storeCode, onClose }: Props) {
  const [location, setLocation] = useState(LOCATIONS[0].value)
  const [scope, setScope] = useState<Scope>('legacy')
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<SheetError | null>(null)

  useEffect(() => {
    if (!open) return
    setError(null)
    const preferred = LOCATIONS.find(l => l.value.startsWith(`${storeCode}:`))
    if (preferred) setLocation(preferred.value)
  }, [open, storeCode])

  const [store, locationCode] = location.split(':')

  async function handleDownload() {
    setPending(true)
    setError(null)
    try {
      const params = new URLSearchParams({ store_code: store, location: locationCode, scope })
      const res = await client.get(`/products/count-sheet?${params}`, { responseType: 'blob' })
      const url = window.URL.createObjectURL(new Blob([res.data]))
      const a = document.createElement('a')
      a.href = url
      a.download = `count_sheet_${store}_${locationCode}_${scope}_${new Date().toISOString().slice(0, 10)}.csv`
      document.body.appendChild(a)
      a.click()
      document.body.removeChild(a)
      window.URL.revokeObjectURL(url)
      onClose()
    } catch (err) {
      setError(await readSheetError(err, store))
    } finally {
      setPending(false)
    }
  }

  return <Modal title="Download count sheet" open={open}
    okText="Download" onOk={handleDownload} confirmLoading={pending}
    okButtonProps={{ 'aria-label': 'Download count sheet' }}
    closable={!pending} maskClosable={!pending} keyboard={!pending}
    cancelButtonProps={{ disabled: pending }}
    onCancel={() => { if (!pending) onClose() }}>
    <Space direction="vertical" size={12} style={{ width: '100%' }}>
      <Text type="secondary">
        Blind opening-count sheet: no expected quantities. Write whole native units in the count column,
        0 when nothing is found, and keep product_id, SKU and unit unchanged.
      </Text>
      <div>
        <div style={{ marginBottom: 4 }}>Location</div>
        <Select aria-label="Count sheet location" value={location} disabled={pending}
          onChange={v => { setLocation(v); setError(null) }} options={LOCATIONS} style={{ width: '100%' }} />
      </div>
      <div>
        <div style={{ marginBottom: 4 }}>Products</div>
        <Select aria-label="Count sheet scope" value={scope} disabled={pending}
          onChange={v => { setScope(v); setError(null) }} style={{ width: '100%' }}
          options={[
            { value: 'legacy', label: `Legacy stock at ${store}` },
            { value: 'catalog', label: 'Whole catalog' },
          ]} />
      </div>
      {error && <Alert role="alert" type="error" showIcon message={error.message} description={error.hint} />}
    </Space>
  </Modal>
}

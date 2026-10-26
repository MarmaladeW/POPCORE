import { useState } from 'react'
import { Popconfirm, message } from 'antd'
import { Button } from '@/components/ui/button'
import client from '../../api/client'

// Only products we sell confirmed designs of are a series (owner, 2026-10-26). While counting, a manager turns a
// product that doesn't back into a plain one. Refused by the server if any design already has stock or sales.
export default function RemoveDesigns({ productId, designCount, onDone }: { productId: number; designCount: number; onDone: () => void }) {
  const [busy, setBusy] = useState(false)
  if (!designCount) return null
  async function remove() {
    setBusy(true)
    try {
      const { data } = await client.post<{ removed_designs: number }>(`/products/${productId}/designs/remove`)
      message.success(`Removed ${data.removed_designs} designs. This product is plain again.`)
      onDone()
    } catch (cause: any) {
      message.error(cause?.response?.data?.error || 'Could not remove the designs. Nothing was changed.')
    } finally {
      setBusy(false)
    }
  }
  return <Popconfirm title="不卖确认款？ Not selling confirmed designs?" okText="Remove designs" cancelText="Keep"
    description={`Removes this product's ${designCount} confirmed designs. Its single box and sealed set stay. Designs with stock or sales are never removed.`}
    onConfirm={remove}>
    <Button variant="outline" disabled={busy}>不卖确认款 · Remove designs</Button>
  </Popconfirm>
}

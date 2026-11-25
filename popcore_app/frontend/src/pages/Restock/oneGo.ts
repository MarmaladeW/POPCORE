import client from '../../api/client'
import type { RestockSession } from './index'

// 搬下来了 Done (owner, 2026-11-20: small daily actions should be handy): one person who brings the goods down
// themselves does the whole sheet in one tap: submit, pick what was asked, send and receive it on the floor. The
// same steps as by hand, one after another, so if one stops the sheet waits at that step for the usual screens.
// Its in-between steps are quiet in 动态: the line people see is the restock arriving on the floor.
const quiet = { headers: { 'X-Popcore-Quiet': '1' } }

export async function bringDownAll(sessionId: number, step: (text: string) => void = () => undefined) {
  step('提交 Submitting')
  await client.post(`/restock/session/${sessionId}/submit`, undefined, quiet)
  let session = (await client.get<RestockSession>(`/restock/session/${sessionId}`)).data
  step('拣货 Picking')
  for (const item of session.items) {
    await client.patch(`/restock/items/${item.id}/pick`, { pick_status: 'found', found_qty: item.requested_qty }, quiet)
  }
  step('搬下去 Sending')
  await client.post(`/restock/session/${sessionId}/pick`, {
    expected_version: session.delivery?.version ?? 1,
    lines: session.items.map((item, index) => ({ line_no: index + 1, quantity: item.requested_qty })),
  }, { headers: { 'Idempotency-Key': crypto.randomUUID(), 'X-Popcore-Quiet': '1' } })
  session = (await client.get<RestockSession>(`/restock/session/${sessionId}`)).data
  const delivery = session.delivery
  if (!delivery) throw new Error('The restock was sent, but its delivery did not load: finish it in 收货 Receive.')
  step('上架 Receiving')
  const lines = delivery.lines.filter(line => line.outstanding_transit > 0)
    .map(line => ({ line_no: line.line_no, quantity: line.outstanding_transit }))
  if (lines.length) {
    await client.post(`/restock/session/${sessionId}/receive`, { expected_version: delivery.version, lines },
      { headers: { 'Idempotency-Key': crypto.randomUUID() } })
  }
}

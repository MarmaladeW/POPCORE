import { useState } from 'react'
import { Alert, Button, Input } from 'antd'
import { useHasRole } from '../../auth/useRole'
import useCheckoutMutation from './useCheckoutMutation'
import type { CloverFailure } from './checkoutParts'

/** Clover orders that could not be imported (owner, 2026-10-27). Closing waits for each until it imports (fix the
 *  cause, then refresh) or a manager says how it was handled; a resolved order is never imported afterwards. */
export default function CloverFailures({ failures, storeId, disabled, resolved }: { failures: CloverFailure[]; storeId: number; disabled: boolean; resolved: () => void }) {
  const manager = useHasRole('manager')
  const [reasons, setReasons] = useState<Record<string, string>>({})
  const mutation = useCheckoutMutation(() => { setReasons({}); resolved() })
  if (!failures.length) return null
  return <section className="co-clover-failures" aria-label="Clover orders not in POPCORE">
    <Alert type="warning" showIcon message={`${failures.length === 1 ? 'A Clover order' : `${failures.length} Clover orders`} did not reach POPCORE`}
      description={manager ? 'Closing waits for each one. Fix the cause and refresh, or record how it was handled.' : 'Closing waits for each one. Ask a manager to resolve it.'} />
    {mutation.notice}
    <ul>{failures.map(failure => <li key={failure.order_id}>
      <strong className="co-clover-id">{failure.order_id}</strong> · {failure.business_date}<br /><small className="co-muted">{failure.error}</small>
      {manager && <div className="co-action-pair">
        <Input aria-label={`How Clover order ${failure.order_id} was handled`} placeholder="For example: recorded with Record sale" maxLength={300}
          value={reasons[failure.order_id] ?? ''} disabled={disabled || mutation.pending} onChange={event => setReasons(old => ({ ...old, [failure.order_id]: event.target.value }))} />
        <Button disabled={disabled || mutation.pending || !reasons[failure.order_id]?.trim()} loading={mutation.saving}
          onClick={() => mutation.run(`/checkouts/clover/${failure.order_id}/resolve`, { store_id: storeId, reason: reasons[failure.order_id].trim() })}>Resolve</Button>
      </div>}
    </li>)}</ul>
  </section>
}

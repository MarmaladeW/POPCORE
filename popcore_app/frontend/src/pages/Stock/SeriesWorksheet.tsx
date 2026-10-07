import { Alert, Button, Drawer, Space } from 'antd'
import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import type { InventoryLocation } from '../../api/goods'
import type { InventorySeries } from '../../api/inventorySeries'
import { torontoDate } from '../Dashboard/todayPresentation'
import useCheckoutMutation from '../Sales/useCheckoutMutation'
import './seriesWorksheet.css'

type Props = {
  mode: 'count' | 'receive'
  series: InventorySeries
  locations: InventoryLocation[]
  onClose: () => void
}

export default function SeriesWorksheet({mode, series, locations, onClose}: Props) {
  const navigate = useNavigate()
  const [locationId, setLocationId] = useState('')
  const [quantities, setQuantities] = useState<Record<number, string>>({})
  const counting = mode === 'count'
  const designs = series.products.filter(product => product.stock_form === 'confirmed_design')
  const location = locations.find(item => item.id === Number(locationId) && item.opening_verified)
  const entered = designs.filter(product => (quantities[product.id] ?? '') !== '')
  const ready = !!location && entered.length > 0 && entered.every(product =>
    product.identity_status === 'verified' && product.stock_unit === 'piece' && !!product.design_name?.trim()
    && Number.isSafeInteger(Number(quantities[product.id])) && Number(quantities[product.id]) >= (counting ? 0 : 1))
  const mutation = useCheckoutMutation(result => {
    navigate(counting ? `/goods/counts?count_id=${result.id}` : `/goods/receiving?receipt_id=${result.id}`)
  })
  const locked = mutation.pending || mutation.saving
  useEffect(() => {
    window.dispatchEvent(new CustomEvent('popcore:checkout-busy', {detail: locked}))
    return () => { window.dispatchEvent(new CustomEvent('popcore:checkout-busy', {detail: false})) }
  }, [locked])

  function review() {
    if (!ready || !location || locked) return
    const lines = entered.map(product => ({product_id: product.id, unit: product.stock_unit,
      ...(counting ? {observed_quantity: Number(quantities[product.id])} : {
        expected_quantity: null, saleable_quantity: Number(quantities[product.id]), damaged_quantity: 0, hold_quantity: 0,
      }),
    }))
    void mutation.run(counting ? '/goods/counts' : '/goods/receipts', {
      ...(counting ? {location_id: location.id, disposition: 'saleable'} : {store_id: location.store_id, destination_location_id: location.id}),
      business_date: torontoDate(), lines,
    })
  }

  return <Drawer rootClassName="pc-inventory-drawer pc-series-worksheet" title={counting ? 'Count series' : 'Receive series'}
    width="min(100%, 720px)" open onClose={() => { if (!locked) onClose() }}
    keyboard={!locked} maskClosable={!locked} closable={!locked}
    footer={<div className="pc-worksheet-footer"><p aria-live="polite">{entered.length} of {designs.length} designs entered</p>
      <Space wrap><Button type="primary" disabled={!ready || locked} loading={mutation.saving} onClick={review}>{counting ? 'Review count' : 'Review receipt'}</Button>
        <Button disabled={locked} onClick={onClose}>Cancel</Button></Space></div>}>
    <div className="pc-series-form">
      <h2 className="pc-worksheet-title">{series.name}</h2>
      <p>{counting ? 'Count saleable pieces at one location. Leave designs you have not counted blank; enter 0 when none are present.'
        : 'Enter the saleable pieces received for each design. Leave designs outside this shipment blank. You can review condition quantities in the receipt.'}</p>
      <p className="pc-worksheet-draft-note">This saves a draft for review. Stock stays unchanged until {counting ? 'the count is submitted and approved' : 'the receipt is posted'}.</p>
      {mutation.notice}
      <fieldset disabled={locked}>
        <label>Location<select aria-label="Location" value={locationId} onChange={event => { setLocationId(event.target.value); setQuantities({}) }}>
          <option value="">Choose a reviewed location</option>
          {locations.map(item => <option key={item.id} value={item.id} disabled={!item.opening_verified}>{item.name}{item.opening_verified ? '' : ' · Opening unreviewed'}</option>)}
        </select></label>
        {!locations.some(item => item.opening_verified) && <Alert type="info" message="A reviewed location is needed before entering quantities." />}
        <table className="pc-worksheet-table"><thead><tr><th scope="col">Design</th><th scope="col">{counting ? 'Counted' : 'Received'}</th></tr></thead>
          <tbody>{designs.map(product => {
            const name = product.design_name || product.name
            const verified = product.identity_status === 'verified' && product.stock_unit === 'piece' && !!product.design_name?.trim()
            return <tr key={product.id}><th scope="row"><span>{name}</span><small>{product.sku}</small>{!verified && <small>Identity needs review</small>}</th>
              <td><input type="number" inputMode="numeric" step="1" min={counting ? 0 : 1} aria-label={`${counting ? 'Count' : 'Receive'} ${name}`}
                value={quantities[product.id] ?? ''} disabled={!location || !verified}
                onChange={event => setQuantities(current => ({...current, [product.id]: event.target.value}))} /><span>{product.stock_unit === 'box' ? 'boxes' : `${product.stock_unit || 'unit'}s`}</span></td></tr>
          })}</tbody></table>
        {!designs.length && <p>No confirmed designs are available in this series.</p>}
      </fieldset>
    </div>
  </Drawer>
}
